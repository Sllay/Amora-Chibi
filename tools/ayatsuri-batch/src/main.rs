//! Headless Ayatsuri2D prototype: layered PNG -> parameter-bound Cubism model.
//! Real deformation keyforms for eyes, iris, eyelids, mouth and face angle.
//! This is still a test rig, not a finalized facial mesh or soft-body simulation.

use std::{collections::{HashMap, HashSet}, env, fs, path::{Component, Path, PathBuf}};
use ayatsuri_format::model::{ArtMesh, BindingAxis, Deformer, DeformerKind, Parameter, RotationData, WarpData, Binding, PhysicsIo, PhysicsInput, PhysicsOutput, PhysicsNode, PhysicsGroup, PhysicsRig, PhysicsRange};
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



fn add_global_squash(model: &mut ayatsuri_format::model::Model, param: u32) {
    // One shared 4x4 lattice deforms all art consistently. This is more
    // efficient than multiplying every eye/iris mesh's Cartesian keyform axes.
    let (extent, cols, rows)=(0.65f32,4u32,4u32);
    let mut positions=Vec::new();
    for amount in [-1.0f32,0.,1.] {
        for row in 0..=rows {
            for col in 0..=cols {
                let x=-extent+2.*extent*(col as f32/cols as f32);
                let y=-extent+2.*extent*(row as f32/rows as f32);
                positions.extend([x*(1.-0.027*amount),y*(1.+0.038*amount)]);
            }
        }
    }
    let parent=0u32; // Root rotation/respiration deformer was already added.
    let warp_index=model.deformers.len() as u32;
    model.deformers.push(Deformer {
        id:"AmoraSoftBodyLattice".into(),parent_part:None,
        parent_deformer:Some(parent),visible:true,enabled:true,
        binding:Binding {axes:vec![BindingAxis {
            parameter:param,keys:vec![-1.,0.,1.]
        }],curves:vec![]},
        kind:DeformerKind::Warp(WarpData {
            rows,cols,quad:true,opacities:vec![1.;3],positions,
        }),
        multiply:Vec::new(),screen:Vec::new(),blend_shapes:Vec::new(),
    });
    // Meshes below a warp are expressed in the warp's [0,1]^2 domain,
    // not canvas coordinates; all of their keyforms require conversion.
    for mesh in &mut model.art_meshes {
        for p in mesh.positions.chunks_exact_mut(2) {
            p[0]=(p[0]+extent)/(2.*extent);
            p[1]=(p[1]+extent)/(2.*extent);
        }
        mesh.parent_deformer=Some(warp_index);
    }
}

fn attach_soft_physics(model: &mut ayatsuri_format::model::Model) {
    let nodes=|| vec![
        PhysicsNode {x:0.,y:0.,mobility:0.95,delay:1.0,acceleration:1.,radius:0.},
        PhysicsNode {x:0.,y:1.,mobility:0.84,delay:1.25,acceleration:0.75,radius:1.},
        PhysicsNode {x:0.,y:1.75,mobility:0.89,delay:1.5,acceleration:0.75,radius:0.75},
    ];
    let norm_position=PhysicsRange {min:-1.,default:0.,max:1.};
    let norm_angle=PhysicsRange {min:-30.,default:0.,max:30.};
    let sources=[
        ("IrisInertiaX","ParamEyeBallX","ParamIrisBounceX",PhysicsIo::X,0.55),
        ("IrisInertiaY","ParamEyeBallY","ParamIrisBounceY",PhysicsIo::Y,0.52),
        ("AmoraBodySquash","ParamBodyAngleZ","ParamBodySquash",PhysicsIo::Angle,0.35),
    ];
    let groups=sources.into_iter().map(|(id,source,target,kind,scale)| PhysicsGroup {
        id:id.into(),name:id.into(),
        inputs:vec![PhysicsInput {param:source.into(),weight:100.,kind,reflect:false}],
        outputs:vec![PhysicsOutput {param:target.into(),vertex:2,scale,weight:100.,kind,reflect:false}],
        nodes:nodes(),norm_position,norm_angle,
    }).collect();
    model.physics=Some(PhysicsRig {fps:60.,gravity_x:0.,gravity_y:-1.,wind_x:0.,wind_y:0.,groups});
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
    let iris_bounce_x=par("ParamIrisBounceX")?;
    let iris_bounce_y=par("ParamIrisBounceY")?;
    let squash=par("ParamBodySquash")?;
    let names = imported.mesh_names.clone();
    let regions: HashMap<String,Region> = names.iter().cloned().zip(imported.model.art_meshes.iter().map(Region::of)).collect();
    let idx = |name: &str| names.iter().position(|n| n==name);
    let open_keys = [0., 0.18, 0.50, 1.];
    let look_keys = [-1., 0., 1.];
    for (side, eye_param) in [("L",leye),("R",reye)] {
        let white_name = format!("eye_white_{side}");
        let Some(white_index) = idx(&white_name) else { continue; };
        let eye = regions[&white_name];
        // Local skin from the *original* JPEG: a feathered patch follows the
        // area above/below the sclera as the eyelids meet. This avoids
        // stretching the complete face or horns to close one eye.
        if let Some(i) = idx(&format!("skin_patch_{side}")) {
            let skin=&mut imported.model.art_meshes[i];
            subdivide(skin, 16, 12);
            key_mesh(skin, &[(eye_param,&open_keys)], |v,x,y| {
                let shut=1.-v[0];
                let nx=((x-eye.cx)/(eye.w*0.8).max(0.0001)).abs();
                let ny=((y-eye.cy)/(eye.h*0.85).max(0.0001)).abs();
                let weight=(1.-nx).max(0.)*(1.-ny).max(0.);
                let push=eye.h*0.085*shut*weight;
                (x-(x-eye.cx)*0.045*shut*weight,
                 y + if y<eye.cy {push} else {-push})
            }, |_|1.);
        }
        let white = &mut imported.model.art_meshes[white_index];
        subdivide(white, 10, 6);
        key_mesh(white, &[(eye_param,&open_keys)], |v,x,y| {
            let opening = v[0];
            let seam = eye.cy+eye.h*0.11;
            (x, seam + (y-eye.cy)*(0.035 + 0.965*opening) - eye.h*0.11*opening)
        }, |v| (v[0]*5.0).min(1.));
        let iris_name=format!("iris_{side}");
        if let Some(i)=idx(&iris_name) {
            let iris_region=regions[&iris_name];
            let iris=&mut imported.model.art_meshes[i];
            subdivide(iris, 8, 6);
            // Clip pupils to the white: they cannot stick out on side glances.
            iris.masks=vec![white_index as u32];
            let travel_x = (eye.w-iris_region.w).max(eye.w*0.10).min(eye.w*0.20)*0.42;
            let travel_y = (eye.h-iris_region.h).max(eye.h*0.10).min(eye.h*0.25)*0.32;
            // Inertial bounce channels are driven by exported Cubism physics3.
            // Gentle overshoot and elastic squash accompany gaze transitions.
            key_mesh(iris, &[(eye_param,&open_keys),(look_x,&look_keys),(look_y,&look_keys),
                             (iris_bounce_x,&look_keys),(iris_bounce_y,&look_keys)],
                |v,x,y| {
                    let opening=v[0];
                    let rel_x=x-iris_region.cx;
                    let rel_y=y-iris_region.cy;
                    let bounce_x=v[3]; let bounce_y=v[4];
                    let stretch_x=1.0+(bounce_x.abs()*0.055 + v[1].abs()*0.018);
                    let stretch_y=1.0+(bounce_y.abs()*0.065 - bounce_x.abs()*0.022);
                    let squash=(0.035 + 0.965*opening);
                    let center_y = eye.cy + (iris_region.cy-eye.cy)*squash;
                    (iris_region.cx + rel_x*stretch_x + v[1]*travel_x*opening + bounce_x*eye.w*0.065*opening,
                     center_y + rel_y*stretch_y*squash + v[2]*travel_y*opening + bounce_y*eye.h*0.055*opening)
                }, |v| (v[0]*5.0).min(1.));
        }
        for (kind, upper) in [("upper",true),("lower",false)] {
            let name = format!("{kind}_lid_{side}");
            if let Some(i)=idx(&name) {
                let old=regions[&name];
                let lid=&mut imported.model.art_meshes[i];
                subdivide(lid, 12, 5);
                key_mesh(lid,&[(eye_param,&open_keys)],|v,x,y| {
                    let shut=(1.-v[0]).powf(0.82);
                    let norm=((x-eye.cx)/(eye.w*0.55).max(0.0001)).clamp(-1.,1.);
                    let arc=(1.-norm*norm).max(0.).powi(2);
                    // Upper lashes create the final smiling arch and lead the
                    // blink. Lower lid barely moves: no opposing sliding plates.
                    if upper {
                        let target=eye.cy+eye.h*0.10;
                        let travel=(target-old.cy)*shut*(0.65+0.35*arc);
                        let smile=-eye.h*0.105*shut*arc;
                        (x+(eye.cx-x)*0.018*shut*arc, y+travel+smile)
                    } else {
                        let travel=-eye.h*0.055*shut*(0.55+0.45*arc);
                        (x, y+travel+eye.h*0.016*shut*arc)
                    }
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
                let vertical=((y-mouth_region.cy)/(mouth_region.h*0.5).max(0.0001)).clamp(-1.,1.);
                let upper=(1.-vertical)*0.5;
                let lower=(1.+vertical)*0.5;
                let horizontal=((x-mouth_region.cx)/(mouth_region.w*0.5).max(0.0001)).clamp(-1.,1.);
                let center=(1.-horizontal.abs()).max(0.);
                let spread=1.+0.09*v[0]+0.12*v[1];
                let lip_shift=mouth_region.h*v[0]*(0.12*upper+0.44*lower);
                let arch=-mouth_region.h*v[1]*center*0.13;
                (mouth_region.cx+(x-mouth_region.cx)*spread,
                 y+lip_shift+arch)
            }, |_|1.);
    }
    add_root_motion(&mut imported.model,angle,body_angle,breath);
    add_global_squash(&mut imported.model,squash);
    attach_soft_physics(&mut imported.model);
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
        ("Shy",&[("ParamEyeLOpen",0.58),("ParamEyeROpen",0.58),("ParamMouthForm",0.6),("ParamMouthOpenY",0.08),("ParamEyeBallY",0.3)]),
        ("Curious",&[("ParamEyeLOpen",1.),("ParamEyeROpen",0.76),("ParamMouthForm",0.2),("ParamMouthOpenY",0.1),("ParamEyeBallX",0.42)]),
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


fn create_idle_motion(out: &Path, name: &str) -> Result<String> {
    /// A diagnostic loop: the MOC3 must move even if tracking is disabled.
    /// It is selectable as an idle animation in VTube Studio, not enabled
    /// automatically (so facial tracking is not silently overridden).
    let filename = format!("{name}_IdleTest.motion3.json");
    let path = out.join("motions");
    fs::create_dir_all(&path)?;
    let loops: &[(&str, &[(f32, f32)])] = &[
        ("ParamEyeLOpen",&[(0.,1.),(1.5,1.),(1.61,0.),(1.78,1.),(4.7,1.),(4.82,0.),(4.99,1.),(6.,1.)]),
        ("ParamEyeROpen",&[(0.,1.),(1.5,1.),(1.61,0.),(1.78,1.),(4.7,1.),(4.82,0.),(4.99,1.),(6.,1.)]),
        ("ParamEyeBallX",&[(0.,0.),(2.,0.),(2.8,0.7),(3.7,0.7),(4.3,-0.5),(5.2,0.),(6.,0.)]),
        ("ParamEyeBallY",&[(0.,0.),(1.,0.),(2.,-0.3),(3.,0.),(6.,0.)]),
        ("ParamMouthOpenY",&[(0.,0.),(2.,0.),(2.4,0.5),(2.9,0.),(6.,0.)]),
        ("ParamBreath",&[(0.,0.5),(1.5,0.85),(3.,0.5),(4.5,0.15),(6.,0.5)]),
        ("ParamBodyAngleZ",&[(0.,0.),(1.5,4.),(3.,0.),(4.5,-4.),(6.,0.)]),
        ("ParamAngleZ",&[(0.,0.),(1.5,2.),(3.,0.),(4.5,-2.),(6.,0.)]),
        ("ParamMouthForm",&[(0.,0.),(2.,0.),(2.4,0.75),(2.9,0.),(6.,0.)]),
    ];
    let mut curves=Vec::new();
    let mut points=0usize;
    let mut segments=0usize;
    for (id, keys) in loops {
        let mut data=vec![keys[0].0,keys[0].1];
        points+=keys.len();
        segments+=keys.len()-1;
        for &(time,value) in &keys[1..] {
            data.extend([0.,time,value]);
        }
        curves.push(json!({"Target":"Parameter","Id":id,"Segments":data}));
    }
    let doc=json!({
        "Version":3,
        "Meta":{
            "Duration":6.0,"Fps":30.0,"Loop":true,
            "AreBeziersRestricted":true,
            "CurveCount":curves.len(),"TotalSegmentCount":segments,
            "TotalPointCount":points,"UserDataCount":0,"TotalUserDataSize":0,
        },
        "Curves":curves
    });
    let bytes=serde_json::to_vec_pretty(&doc)?;
    // Validate actual Cubism motion3 syntax through Ayatsuri's decoder.
    let reopened=ayatsuri_import::from_motion3(&bytes,"diagnostic")?;
    if reopened.curves.len()!=loops.len() || !reopened.looped {
        return Err("diagnostic idle motion lost curves or loop metadata".into());
    }
    fs::write(path.join(&filename),bytes)?;
    Ok(format!("motions/{filename}"))
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
        parameter("ParamIrisBounceX", -1.0, 1.0, 0.0),
        parameter("ParamIrisBounceY", -1.0, 1.0, 0.0),
        parameter("ParamMouthOpenY", 0.0, 1.0, 0.0),
        parameter("ParamMouthForm", -1.0, 1.0, 0.0),
        parameter("ParamAngleZ", -30.0, 30.0, 0.0),
        parameter("ParamBodyAngleZ", -10.0, 10.0, 0.0),
        parameter("ParamBreath", 0.0, 1.0, 0.5),
        parameter("ParamBodySquash", -1.0, 1.0, 0.0),
    ]);
    attach_live_rig(&mut imported)?;
    fs::create_dir_all(out)?;
    let moc = to_moc3(&imported.model, ExportVersion::V5_00)?;
    if !moc.starts_with(b"MOC3") {
        return Err("ayatsuri export does not have MOC3 header".into());
    }
    // Prove the generated binary can be read as a Cubism model, not merely
    // identified by the magic header. Check that the active key bindings survive.
    let reopened = ayatsuri_import::from_moc3(&moc)?;
    let authored = imported.model.art_meshes.iter().filter(|m| !m.binding.axes.is_empty()).count();
    let recovered = reopened.art_meshes.iter().filter(|m| !m.binding.axes.is_empty()).count();
    if authored != recovered || reopened.parameters.len() != imported.model.parameters.len() {
        return Err(format!("MOC3 lost keyforms or parameters: {authored} keyed meshes authored, {recovered} recovered").into());
    }
    if reopened.deformers.len() != imported.model.deformers.len() {
        return Err("MOC3 lost the root motion deformer".into());
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
    let physics_filename=format!("{name}.physics3.json");
    let rig=imported.model.physics.as_ref().ok_or("soft-body physics missing")?;
    let physics_data=ayatsuri_import::to_physics3(rig);
    let parsed=ayatsuri_import::from_physics3(&physics_data)?;
    if parsed.groups.len()!=3 || parsed.groups.iter().any(|g| g.inputs.is_empty()||g.outputs.is_empty()) {
        return Err("physics3 did not roundtrip all three inertial controllers".into());
    }
    fs::write(out.join(&physics_filename),physics_data)?;
    let idle_motion = create_idle_motion(out, name)?;
    let model3 = json!({
        "Version": 3,
        "FileReferences": {
            "Moc": format!("{name}.moc3"),
            "Textures": texture_refs,
            "DisplayInfo": cdi_file,
            "Expressions": expressions,
            "Physics": physics_filename,
            "Motions": { "Idle": [{"File":idle_motion, "FadeInTime":0.3, "FadeOutTime":0.3}] },
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
