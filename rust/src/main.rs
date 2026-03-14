fn main() {
    if let Err(err) = rtve_dl_rust::pipeline::run() {
        eprintln!("error: {err:#}");
        std::process::exit(1);
    }
}
