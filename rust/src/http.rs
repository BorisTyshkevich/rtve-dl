use anyhow::{bail, Context, Result};
use reqwest::blocking::Client;
use reqwest::header::{HeaderMap, HeaderValue, ACCEPT, ACCEPT_ENCODING, USER_AGENT};
use serde::de::DeserializeOwned;
use std::time::Duration;

pub struct HttpClient {
    client: Client,
}

impl HttpClient {
    pub fn new() -> Result<Self> {
        let mut headers = HeaderMap::new();
        headers.insert(
            USER_AGENT,
            HeaderValue::from_static(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
            ),
        );
        headers.insert(ACCEPT, HeaderValue::from_static("*/*"));
        headers.insert(
            ACCEPT_ENCODING,
            HeaderValue::from_static("gzip, deflate, br"),
        );
        let client = Client::builder()
            .default_headers(headers)
            .timeout(Duration::from_secs(30))
            .build()
            .context("build HTTP client")?;
        Ok(Self { client })
    }

    pub fn get_text(&self, url: &str) -> Result<String> {
        let response = self
            .client
            .get(url)
            .send()
            .with_context(|| format!("GET {url}"))?;
        let status = response.status();
        if !status.is_success() {
            bail!("GET {url} failed: HTTP {status}");
        }
        response
            .text()
            .with_context(|| format!("decode text {url}"))
    }

    pub fn get_bytes(&self, url: &str) -> Result<Vec<u8>> {
        let response = self
            .client
            .get(url)
            .send()
            .with_context(|| format!("GET {url}"))?;
        let status = response.status();
        if !status.is_success() {
            bail!("GET {url} failed: HTTP {status}");
        }
        Ok(response
            .bytes()
            .with_context(|| format!("read bytes {url}"))?
            .to_vec())
    }

    pub fn get_json<T: DeserializeOwned>(&self, url: &str) -> Result<T> {
        let response = self
            .client
            .get(url)
            .send()
            .with_context(|| format!("GET {url}"))?;
        let status = response.status();
        if !status.is_success() {
            bail!("GET {url} failed: HTTP {status}");
        }
        response
            .json()
            .with_context(|| format!("decode JSON {url}"))
    }
}
