use std::sync::atomic::{AtomicBool, Ordering};
use time::format_description::parse_borrowed;
use time::{OffsetDateTime, UtcOffset};

static DEBUG_ENABLED: AtomicBool = AtomicBool::new(false);

pub fn set_debug(enabled: bool) {
    DEBUG_ENABLED.store(enabled, Ordering::Relaxed);
}

pub fn is_debug() -> bool {
    DEBUG_ENABLED.load(Ordering::Relaxed)
}

pub fn debug(message: impl AsRef<str>) {
    if is_debug() {
        eprintln!("[{}] [DEBUG] {}", ts(), message.as_ref());
    }
}

fn ts() -> String {
    let now = OffsetDateTime::now_local().unwrap_or_else(|_| OffsetDateTime::now_utc());
    let fmt = parse_borrowed::<2>("[hour]:[minute]:[second].[subsecond digits:3]")
        .expect("valid log timestamp format");
    now.to_offset(local_offset()).format(&fmt).unwrap_or_else(|_| "00:00:00.000".to_string())
}

fn local_offset() -> UtcOffset {
    UtcOffset::current_local_offset().unwrap_or(UtcOffset::UTC)
}
