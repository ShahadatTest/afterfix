use std::io::{self, Read};
use std::process::ExitCode;

fn main() -> ExitCode {
    if std::env::args_os().skip(1).next().is_some() {
        let (s, _) = afterfix_verifier::handle_bytes(b"");
        // Sanitized fixed output; arguments are never echoed.
        let s = s.replace("decode_error", "invalid_args");
        println!("{s}");
        return ExitCode::from(2);
    }
    let mut buf = Vec::new();
    let read = io::stdin()
        .take((afterfix_verifier::INPUT_LIMIT + 1) as u64)
        .read_to_end(&mut buf);
    let (out, code) = match read {
        Ok(_) => afterfix_verifier::handle_bytes(&buf),
        Err(_) => afterfix_verifier::handle_bytes(b""),
    };
    println!("{out}");
    ExitCode::from(code as u8)
}
