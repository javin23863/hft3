use std::env;
use std::path::PathBuf;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    // Compile Protocol Buffers
    let proto_file = "proto/edge_features.proto";
    let out_dir = PathBuf::from(env::var("OUT_DIR").unwrap());
    
    prost_build::Config::new()
        .out_dir(&out_dir)
        .compile_protos(&[proto_file], &["proto/"])?;
    
    println!("cargo:rerun-if-changed={}", proto_file);
    
    Ok(())
}
