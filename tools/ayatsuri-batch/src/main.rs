//! Headless Ayatsuri2D proof of concept: layered PNG -> static VTube Studio bundle.
//! This is NOT the finished rig: tracking bindings, blink geometry, squash and
//! physics must be verified before it is labeled usable for streaming.

use std::{collections::HashSet, env, fs, path::{Component, Path, PathBuf}};
use ayatsuri_format::model::Parameter;
use ayatsuri_import::{Blend, Document, ExportVersion, Layer, from_layers, to_moc3};
use serde::Deserialize;
use serde_json::json;

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

#[derive(Deserialize)]
struct Config {
    name: String,
    width: u32,
    height: u32,
    layers: Vec<LayerConfig>,
}
#[derive(Deserialize)]
struct LayerConfig {
    name: String,
    file: String,
    #[serde(default)]
    group: Option<String>,
}

fn load_document(path: &Path) -> Result<(String, Document)> {
    let cfg: Config = serde_json::from_slice(&fs::read(path)?)?;
    if cfg.name.is_empty() || cfg.name.chars().any(|c| !(c.is_ascii_alphanumeric() || c == '_' || c == '-')) {
        return Err("model name must use only ASCII letters, digits, _ and -".into());
    }
    if cfg.width == 0 || cfg.height == 0 || cfg.width > 4096 || cfg.height > 4096 {
        return Err("invalid canvas size (allowed: 1..4096)".into());
    }
    if cfg.layers.is_empty() || cfg.layers.len() > 64 {
        return Err("expected between 1 and 64 PNG layers".into());
    }
    let base = path.parent().unwrap_or(Path::new("."));
    let mut seen = HashSet::new();
    let mut layers = Vec::with_capacity(cfg.layers.len());
    for spec in cfg.layers {
        if !seen.insert(spec.name.clone()) {
            return Err(format!("duplicate layer name: {}", spec.name).into());
        }
        let relative = Path::new(&spec.file);
        if !relative.components().all(|c| matches!(c, Component::Normal(_))) {
            return Err(format!("unsafe relative PNG path: {}", spec.file).into());
        }
        let bytes = fs::read(base.join(relative))?;
        if !bytes.starts_with(b"\x89PNG\r\n\x1a\n") {
            return Err(format!("{}: expected a PNG image", spec.file).into());
        }
        let image = image::load_from_memory(&bytes)?.into_rgba8();
        if image.width() != cfg.width || image.height() != cfg.height {
            return Err(format!("{}: got {}x{}, expected {}x{}",
                spec.file, image.width(), image.height(), cfg.width, cfg.height).into());
        }
        if !image.pixels().any(|px| px.0[3] > 0) {
            return Err(format!("{}: entirely transparent layer", spec.file).into());
        }
        layers.push(Layer {
            name: spec.name,
            groups: spec.group.into_iter().collect(),
            x: 0,
            y: 0,
            width: cfg.width,
            height: cfg.height,
            rgba: image.into_raw(),
            visible: true,
            opacity: 1.0,
            clip_to_below: false,
            blend: Blend::NORMAL,
        });
    }
    Ok((cfg.name, Document {
        width: cfg.width, height: cfg.height, layers, folders: Vec::new(),
    }))
}

fn parameter(id: &str, min: f32, max: f32, default: f32) -> Parameter {
    Parameter {
        id: id.into(), min, max, default, repeat: false,
        decimals: 3, blend_shape: false,
    }
}

fn compile(name: &str, doc: &Document, out: &Path) -> Result<()> {
    let mut imported = from_layers(doc)?;
    if imported.skipped != 0 || imported.scaled != 0 {
        return Err(format!("layers unexpectedly skipped={} or scaled={}",
            imported.skipped, imported.scaled).into());
    }
    // These IDs are declared only. No misleading animation bindings are added
    // until the eyelid art and actual deformation keyforms have been tested.
    imported.model.parameters.extend([
        parameter("ParamEyeLOpen", 0.0, 1.0, 1.0),
        parameter("ParamEyeROpen", 0.0, 1.0, 1.0),
        parameter("ParamEyeBallX", -1.0, 1.0, 0.0),
        parameter("ParamEyeBallY", -1.0, 1.0, 0.0),
        parameter("ParamMouthOpenY", 0.0, 1.0, 0.0),
        parameter("ParamAngleZ", -30.0, 30.0, 0.0),
        parameter("ParamBodyAngleZ", -10.0, 10.0, 0.0),
        parameter("ParamBreath", 0.0, 1.0, 0.0),
    ]);
    fs::create_dir_all(out)?;
    let moc = to_moc3(&imported.model, ExportVersion::V5_00)?;
    if !moc.starts_with(b"MOC3") {
        return Err("ayatsuri export does not have MOC3 header".into());
    }
    fs::write(out.join(format!("{name}.moc3")), &moc)?;

    let tex_dir_name = format!("{name}.textures");
    let tex_dir = out.join(&tex_dir_name);
    fs::create_dir_all(&tex_dir)?;
    let mut texture_refs = Vec::new();
    for (i, bytes) in imported.textures.iter().enumerate() {
        let filename = format!("texture_{i:02}.png");
        fs::write(tex_dir.join(&filename), bytes)?;
        texture_refs.push(format!("{tex_dir_name}/{filename}"));
    }
    let display_info = json!({
        "Version": 3,
        "Parameters": imported.model.parameters.iter().map(|p| json!({
            "Id": p.id, "GroupId": "", "Name": p.id,
        })).collect::<Vec<_>>(),
        "ParameterGroups": [],
        "Parts": imported.model.parts.iter().enumerate().map(|(i, p)| json!({
            "Id": p.id, "Name": imported.part_names.get(i).cloned().unwrap_or_default()
        })).collect::<Vec<_>>(),
        "CombinedParameters": [],
    });
    let cdi_file = format!("{name}.cdi3.json");
    fs::write(out.join(&cdi_file), serde_json::to_vec_pretty(&display_info)?)?;
    let model3 = json!({
        "Version": 3,
        "FileReferences": {
            "Moc": format!("{name}.moc3"),
            "Textures": texture_refs,
            "DisplayInfo": cdi_file,
        },
        "Groups": [
            { "Target": "Parameter", "Name": "EyeBlink", "Ids": ["ParamEyeLOpen", "ParamEyeROpen"] },
            { "Target": "Parameter", "Name": "LipSync", "Ids": ["ParamMouthOpenY"] },
        ],
    });
    fs::write(out.join(format!("{name}.model3.json")), serde_json::to_vec_pretty(&model3)?)?;
    println!("Static export OK: {} meshes, {} textures, {} bytes MOC3 (NO ANIMATION YET)",
        imported.model.art_meshes.len(), imported.textures.len(), moc.len());
    Ok(())
}

fn run() -> Result<()> {
    let args: Vec<String> = env::args().collect();
    if args.len() != 3 {
        return Err("usage: amora-ayatsuri-batch <manifest.json> <output-directory>".into());
    }
    let (name, document) = load_document(Path::new(&args[1]))?;
    compile(&name, &document, &PathBuf::from(&args[2]))
}

fn main() {
    if let Err(error) = run() {
        eprintln!("ERROR: {error}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn can_export_two_synthetic_layers_without_a_gui() {
        let layer = |name: &str| Layer {
            name: name.into(), groups: Vec::new(), x: 0, y: 0,
            width: 8, height: 8, rgba: vec![255u8; 8 * 8 * 4],
            visible: true, opacity: 1.0, clip_to_below: false, blend: Blend::NORMAL,
        };
        let document = Document {
            width: 8, height: 8, folders: Vec::new(),
            layers: vec![layer("body"), layer("eye_white_left")],
        };
        let root = std::env::temp_dir().join(format!("amora-ayatsuri-test-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        compile("AmoraTest", &document, &root).expect("headless export");
        let moc = fs::read(root.join("AmoraTest.moc3")).expect("moc3");
        assert!(moc.starts_with(b"MOC3"));
        let model3: serde_json::Value = serde_json::from_slice(
            &fs::read(root.join("AmoraTest.model3.json")).expect("model3")).expect("json");
        let textures = model3["FileReferences"]["Textures"].as_array().expect("textures array");
        assert!(!textures.is_empty());
        assert!(root.join(textures[0].as_str().expect("texture ref")).exists());
        let _ = fs::remove_dir_all(&root);
    }
}
