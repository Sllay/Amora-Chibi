//! Headless Ayatsuri2D prototype: layered PNG -> parameter-bound Cubism model.
//! Real deformation keyforms for eyes, iris, eyelids, mouth and face angle.
//! This is still a test rig, not a finalized facial mesh or soft-body simulation.

use std::{collections::{HashMap, HashSet}, env, fs, path::{Component, Path, PathBuf}};
use ayatsuri_format::model::{ArtMesh, BindingAxis, Deformer, DeformerKind, Parameter, RotationData, Binding};
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


#[derive(Clone, Copy)]
struct Region { cx: f32, cy: f32, w: f32, h: f32 }
impl Region {
    fn of(m: &ArtMesh) -> Self {
        let (mut x0, mut y0, mut x1, mut y1) = (f32::INFINITY, f32::INFINITY, f32::NEG_INFINITY, f32::NEG_INFINITY);
        for p in m.positions.chunks_exact(2).take(m.vertex_count as usize) {
            x0 = x0.min(p[0]); y0 = y0.min(p[1]); x1 = x1.max(p[0]); y1 = y1.max(p[1]);
        }
        Self { cx: (x0+x1)*0.5, cy:(y0+y1)*0.5, w:x1-x0, h:y1-y0 }
    }
}

fn bilinear(v: &[[f32; 2]; 4], u: f32, t: f32) -> [f32; 2] {
    let mut o = [0.; 2];
    for i in 0..2 {
        o[i] = v[0][i]*(1.-u)*(1.-t) + v[1][i]*u*(1.-t) +
            v[2][i]*u*t + v[3][i]*(1.-u)*t;
    }
    o
}

/// From_layers produces a single textured quad per layer. Subdivision lets
/// eyelids curve without baking different pictures for each expression.
fn subdivide(m: &mut ArtMesh, cols: usize, rows: usize) {
    assert_eq!(m.vertex_count, 4, "expected imported 4-point layer");
    assert_eq!(m.positions.len(), 8);
    let p = [[m.positions[0], m.positions[1]], [m.positions[2], m.positions[3]],
             [m.positions[4], m.positions[5]], [m.positions[6], m.positions[7]]];
    let uv = [[m.uvs[0],m.uvs[1]], [m.uvs[2],m.uvs[3]],
              [m.uvs[4],m.uvs[5]], [m.uvs[6],m.uvs[7]]];
    m.positions.clear();
    m.uvs.clear();
    m.indices.clear();
    for y in 0..=rows {
        for x in 0..=cols {
            let u = x as f32 / cols as f32;
            let v = y as f32 / rows as f32;
            m.positions.extend(bilinear(&p, u, v));
            m.uvs.extend(bilinear(&uv, u, v));
        }
    }
    for y in 0..rows {
        for x in 0..cols {
            let a = (y*(cols+1)+x) as u16;
            let b = a+1;
            let d = a+(cols+1) as u16;
            let c = d+1;
            m.indices.extend([a,b,c,a,c,d]);
        }
    }
    m.vertex_count = ((cols+1)*(rows+1)) as u32;
}

/// Bind geometric shapes with correct Cubism key ordering (first axis varies
/// fastest). Opacities are keyed too, so closed whites do not leak past lids.
fn key_mesh(
    m: &mut ArtMesh,
    axes: &[(u32, &[f32])],
    shape: impl Fn(&[f32], f32, f32) -> (f32, f32),
    opacity: impl Fn(&[f32]) -> f32,
) {
    let base = m.positions.clone();
    let n = axes.iter().map(|(_,v)| v.len()).product::<usize>();
    m.binding = Binding {
        axes: axes.iter().map(|(p,k)| BindingAxis {parameter:*p,keys:k.to_vec()}).collect(),
        curves: vec![],
    };
    m.positions = Vec::with_capacity(base.len()*n);
    m.opacities = Vec::with_capacity(n);
    m.draw_orders = vec![500.; n];
    for key_index in 0..n {
        let mut k = key_index;
        let values: Vec<f32> = axes.iter().map(|(_,keys)| {
            let v = keys[k % keys.len()];
            k /= keys.len();
            v
        }).collect();
        for point in base.chunks_exact(2) {
            let (x,y) = shape(&values, point[0],point[1]);
            m.positions.extend([x,y]);
        }
        m.opacities.push(opacity(&values).clamp(0.,1.));
    }
}

fn add_root_motion(model: &mut ayatsuri_format::model::Model, angle: u32, body_angle: u32, breath: u32) {
    let angles = [-30., 0., 30.];
    let leans = [-10., 0., 10.];
    let breaths = [0., 0.5, 1.];
    let mut all_angles = Vec::new();
    let mut scales = Vec::new();
    for b in breaths {
        for l in leans {
            for a in angles {
                all_angles.push(a + l);
                scales.push(1. + (b - 0.5) * 0.018);
            }
        }
    }
    let count = all_angles.len();
    model.deformers.push(Deformer {
        id: "AmoraRootMotion".into(), parent_part: None,
        parent_deformer: None, visible: true, enabled: true,
        binding: Binding {
            axes: vec![
                BindingAxis { parameter:angle, keys:angles.to_vec() },
                BindingAxis { parameter:body_angle, keys:leans.to_vec() },
                BindingAxis { parameter:breath, keys:breaths.to_vec() },
            ], curves:vec![],
        },
        kind: DeformerKind::Rotation(RotationData {
            base_angle:0., opacities:vec![1.; count], angles:all_angles,
            origins_x:vec![0.;count], origins_y:vec![0.;count],
            scales, reflect_x:vec![false;count],reflect_y:vec![false;count],
        }),
        multiply:Vec::new(), screen:Vec::new(), blend_shapes:Vec::new(),
    });
    for m in &mut model.art_meshes { m.parent_deformer = Some(0); }
}

/// Every changed layer stays derived from the user's originals. There is no
/// fabricated additional eye art or baked animation sheet.
fn attach_live_rig(imported: &mut ayatsuri_import::Imported) -> Result<()> {
    let par = |id: &str| -> Result<u32> {
        imported.model.parameters.iter().position(|p| p.id == id)
            .map(|i| i as u32).ok_or_else(|| format!("missing rig parameter: {id}").into())
    };
    let leye=par("ParamEyeLOpen")?; let reye=par("ParamEyeROpen")?;
    let look_x=par("ParamEyeBallX")?; let look_y=par("ParamEyeBallY")?;
    let mouth_open=par("ParamMouthOpenY")?;
    let mouth_form=par("ParamMouthForm")?;
    let angle=par("ParamAngleZ")?; let body_angle=par("ParamBodyAngleZ")?;
    let breath=par("ParamBreath")?;
    let names = imported.mesh_names.clone();
    let regions: HashMap<String,Region> = names.iter().cloned().zip(imported.model.art_meshes.iter().map(Region::of)).collect();
    let idx = |name: &str| names.iter().position(|n| n==name);
    let open_keys = [0., 0.25, 0.6, 1.];
    let look_keys = [-1., 0., 1.];
    for (side, eye_param) in [("L",leye),("R",reye)] {
        let white_name = format!("eye_white_{side}");
        let Some(white_index) = idx(&white_name) else { continue; };
        let eye = regions[&white_name];
        let white = &mut imported.model.art_meshes[white_index];
        subdivide(white, 10, 6);
        key_mesh(white, &[(eye_param,&open_keys)], |v,x,y| {
            let opening = v[0];
            (x, eye.cy + (y-eye.cy)*(0.045 + 0.955*opening))
        }, |v| v[0].min(1.));
        let iris_name=format!("iris_{side}");
        if let Some(i)=idx(&iris_name) {
            let iris_region=regions[&iris_name];
            let iris=&mut imported.model.art_meshes[i];
            subdivide(iris, 8, 6);
            // Clip pupils to the white: they cannot stick out on side glances.
            iris.masks=vec![white_index as u32];
            let travel_x = (eye.w-iris_region.w).max(eye.w*0.10).min(eye.w*0.20)*0.42;
            let travel_y = (eye.h-iris_region.h).max(eye.h*0.10).min(eye.h*0.25)*0.32;
            key_mesh(iris, &[(eye_param,&open_keys),(look_x,&look_keys),(look_y,&look_keys)],
                |v,x,y| {
                    let opening=v[0];
                    (x + v[1]*travel_x*opening,
                     eye.cy + (y-eye.cy)*(0.045+0.955*opening) + v[2]*travel_y*opening)
                }, |v| v[0].min(1.));
        }
        for (kind, upper) in [("upper",true),("lower",false)] {
            let name = format!("{kind}_lid_{side}");
            if let Some(i)=idx(&name) {
                let old=regions[&name];
                let lid=&mut imported.model.art_meshes[i];
                subdivide(lid, 12, 5);
                key_mesh(lid,&[(eye_param,&open_keys)],|v,x,y| {
                    let shut=1.-v[0];
                    let target=eye.cy + if upper { -eye.h*0.065 } else { eye.h*0.065 };
                    let arch=(1.-((x-eye.cx)/(eye.w*0.55).max(0.0001)).powi(2)).max(0.)
                        * eye.h * 0.09 * if upper {-1.} else {1.};
                    (x, y+(target-old.cy)*shut+arch*shut)
                }, |_| 1.);
            }
        }
    }
    if let Some(i)=idx("mouth") {
        let mouth_region=regions["mouth"];
        let mouth=&mut imported.model.art_meshes[i];
        subdivide(mouth, 8, 5);
        let open_keys = [0., 0.5, 1.];
        let form_keys = [-1., 0., 1.];
        key_mesh(mouth,&[(mouth_open,&open_keys),(mouth_form,&form_keys)],
            |v,x,y| {
                (mouth_region.cx+(x-mouth_region.cx)*(1.+0.15*v[1]),
                 mouth_region.cy+(y-mouth_region.cy)*(1.+0.60*v[0]))
            }, |_|1.);
    }
    add_root_motion(&mut imported.model,angle,body_angle,breath);
    for m in &imported.model.art_meshes {
        let n=m.binding.keyform_count();
        if m.positions.len()!=n*m.vertex_count as usize*2 ||
           m.opacities.len()!=n || m.draw_orders.len()!=n {
            return Err(format!("broken keyform arrays for {}",m.id).into());
        }
    }
    Ok(())
}

fn export_expressions(root: &Path, name: &str) -> Result<Vec<serde_json::Value>> {
    let dir=root.join("expressions");
    fs::create_dir_all(&dir)?;
    let presets: &[(&str,&[(&str,f32)])]=&[
        ("Happy",&[("ParamEyeLOpen",0.87),("ParamEyeROpen",0.87),("ParamMouthForm",0.8),("ParamMouthOpenY",0.28)]),
        ("Surprised",&[("ParamEyeLOpen",1.),("ParamEyeROpen",1.),("ParamMouthForm",-0.5),("ParamMouthOpenY",1.)]),
        ("Sleepy",&[("ParamEyeLOpen",0.27),("ParamEyeROpen",0.27),("ParamMouthForm",-0.3),("ParamMouthOpenY",0.)]),
        ("Annoyed",&[("ParamEyeLOpen",0.54),("ParamEyeROpen",0.54),("ParamMouthForm",-0.85),("ParamMouthOpenY",0.)]),
        ("WinkLeft",&[("ParamEyeLOpen",0.),("ParamEyeROpen",1.),("ParamMouthForm",0.7)]),
        ("WinkRight",&[("ParamEyeLOpen",1.),("ParamEyeROpen",0.),("ParamMouthForm",0.7)]),
    ];
    let mut files=Vec::new();
    for (preset, params) in presets {
        let filename=format!("{name}_{preset}.exp3.json");
        fs::write(dir.join(&filename),serde_json::to_vec_pretty(&json!({
            "Type":"Live2D Expression",
            "FadeInTime":0.25,
            "FadeOutTime":0.25,
            "Parameters": params.iter().map(|(id,value)|json!({
                "Id":id,"Value":value,"Blend":"Overwrite"
            })).collect::<Vec<_>>(),
        }))?)?;
        files.push(json!({"Name":preset,"File":format!("expressions/{filename}")}));
    }
    Ok(files)
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
        parameter("ParamMouthForm", -1.0, 1.0, 0.0),
        parameter("ParamAngleZ", -30.0, 30.0, 0.0),
        parameter("ParamBodyAngleZ", -10.0, 10.0, 0.0),
        parameter("ParamBreath", 0.0, 1.0, 0.5),
    ]);
    attach_live_rig(&mut imported)?;
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
    let expressions = export_expressions(out, name)?;
    let model3 = json!({
        "Version": 3,
        "FileReferences": {
            "Moc": format!("{name}.moc3"),
            "Textures": texture_refs,
            "DisplayInfo": cdi_file,
            "Expressions": expressions,
        },
        "Groups": [
            { "Target": "Parameter", "Name": "EyeBlink", "Ids": ["ParamEyeLOpen", "ParamEyeROpen"] },
            { "Target": "Parameter", "Name": "LipSync", "Ids": ["ParamMouthOpenY"] },
        ],
    });
    fs::write(out.join(format!("{name}.model3.json")), serde_json::to_vec_pretty(&model3)?)?;
    println!("Parameter-bound rig exported: {} meshes, {} textures, {} bytes MOC3",
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
