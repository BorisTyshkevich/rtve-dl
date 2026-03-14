use clap::{ArgAction, Parser};

#[derive(Parser, Debug, Clone)]
#[command(name = "rtve", about = "RTVE downloader and subtitle muxer (Rust)")]
pub struct Args {
    #[arg(help = "Positional args: [series_url] selector")]
    pub positionals: Vec<String>,

    #[arg(short = 's', long = "series-slug")]
    pub series_slug: Option<String>,

    #[arg(long, default_value = "mp4")]
    pub quality: String,

    #[arg(short = 'd', long = "debug", action = ArgAction::SetTrue)]
    pub debug: bool,

    #[arg(long = "asr-if-missing", default_value_t = true, action = ArgAction::Set)]
    pub asr_if_missing: bool,

    #[arg(long = "asr-backend", default_value = "auto")]
    pub asr_backend: String,

    #[arg(long = "asr-model", default_value = "small")]
    pub asr_model: String,

    #[arg(long = "asr-device", default_value = "cpu")]
    pub asr_device: String,

    #[arg(long = "asr-compute-type", default_value = "int8")]
    pub asr_compute_type: String,

    #[arg(long = "asr-batch-size", default_value_t = 8)]
    pub asr_batch_size: usize,

    #[arg(long = "asr-vad-method", default_value = "silero")]
    pub asr_vad_method: String,

    #[arg(long = "translation-backend", default_value = "claude")]
    pub translation_backend: String,

    #[arg(long = "claude-model", default_value = "sonnet")]
    pub claude_model: String,

    #[arg(long = "codex-model", default_value = "gpt-5.1-codex-mini")]
    pub codex_model: String,

    #[arg(long = "gemini-model")]
    pub gemini_model: Option<String>,

    #[arg(short = 'm', long = "model")]
    pub model: Option<String>,

    #[arg(long = "codex-chunk-cues", default_value_t = 500)]
    pub codex_chunk_cues: usize,

    #[arg(long = "jobs-codex-chunks", default_value_t = 4)]
    pub jobs_codex_chunks: usize,

    #[arg(long = "sub", action = ArgAction::Append)]
    pub sub_modes: Vec<String>,

    #[arg(long = "default-subtitle", default_value = "refs")]
    pub default_subtitle: String,

    #[arg(long = "video-codec", default_value = "copy")]
    pub video_codec: String,

    #[arg(long = "hevc-device", default_value = "cpu")]
    pub hevc_device: String,

    #[arg(long = "hevc-crf", default_value_t = 18)]
    pub hevc_crf: i32,

    #[arg(long = "hevc-preset", default_value = "slow")]
    pub hevc_preset: String,

    #[arg(long = "subtitle-align", default_value = "off")]
    pub subtitle_align: String,

    #[arg(long = "subtitle-delay", default_value = "auto")]
    pub subtitle_delay: String,

    #[arg(long = "save-auto-delay-asr-dir")]
    pub save_auto_delay_asr_dir: Option<String>,

    #[arg(long = "reset-layer", alias = "reset", action = ArgAction::Append)]
    pub reset_layers: Vec<String>,
}

impl Args {
    pub fn selector(&self) -> Option<String> {
        match self.positionals.len() {
            1 => self.positionals.first().cloned(),
            2 => self.positionals.get(1).cloned(),
            _ => None,
        }
    }

    pub fn resolved_series_url(&self) -> Option<String> {
        match self.positionals.len() {
            2 => self.positionals.first().cloned(),
            1 => std::env::var("RTVE_SERIES_URL").ok(),
            _ => None,
        }
    }

    pub fn resolved_series_slug(&self) -> Option<String> {
        self.series_slug
            .clone()
            .or_else(|| std::env::var("RTVE_SERIES_SLUG").ok())
    }

    pub fn selected_model(&self) -> Option<String> {
        if let Some(model) = &self.model {
            return Some(model.clone());
        }
        match self.translation_backend.as_str() {
            "codex" => Some(self.codex_model.clone()),
            "gemini" => self.gemini_model.clone(),
            _ => Some(self.claude_model.clone()),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::Args;

    fn base_args() -> Args {
        Args {
            positionals: Vec::new(),
            series_slug: None,
            quality: "mp4".to_string(),
            debug: false,
            asr_if_missing: true,
            asr_backend: "auto".to_string(),
            asr_model: "small".to_string(),
            asr_device: "cpu".to_string(),
            asr_compute_type: "int8".to_string(),
            asr_batch_size: 8,
            asr_vad_method: "silero".to_string(),
            translation_backend: "claude".to_string(),
            claude_model: "sonnet".to_string(),
            codex_model: "gpt-5.1-codex-mini".to_string(),
            gemini_model: None,
            model: None,
            codex_chunk_cues: 500,
            jobs_codex_chunks: 4,
            sub_modes: Vec::new(),
            default_subtitle: "refs".to_string(),
            video_codec: "copy".to_string(),
            hevc_device: "cpu".to_string(),
            hevc_crf: 18,
            hevc_preset: "slow".to_string(),
            subtitle_align: "off".to_string(),
            subtitle_delay: "auto".to_string(),
            save_auto_delay_asr_dir: None,
            reset_layers: Vec::new(),
        }
    }

    #[test]
    fn selected_model_uses_gemini_override() {
        let mut args = base_args();
        args.translation_backend = "gemini".to_string();
        args.gemini_model = Some("gemini-2.5-pro".to_string());
        assert_eq!(args.selected_model().as_deref(), Some("gemini-2.5-pro"));
    }

    #[test]
    fn selected_model_prefers_generic_override_for_gemini() {
        let mut args = base_args();
        args.translation_backend = "gemini".to_string();
        args.gemini_model = Some("gemini-2.5-pro".to_string());
        args.model = Some("gemini-2.0-flash".to_string());
        assert_eq!(args.selected_model().as_deref(), Some("gemini-2.0-flash"));
    }

    #[test]
    fn selected_model_allows_gemini_backend_default() {
        let mut args = base_args();
        args.translation_backend = "gemini".to_string();
        assert_eq!(args.selected_model(), None);
    }
}
