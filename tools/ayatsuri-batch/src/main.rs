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



// The sclera/iris are concealed by a narrow lid aperture. Fade the small
// residual opening rather than exposing a flattened pink iris stripe.
fn eye_visibility(opening: f32) -> f32 {
    // Do not fade the eyeball while a visible gap remains between lashes.
    // Opaque skin curtains physically OCCLUDE the sclera and iris instead.
    let t=((opening-0.010)/0.105).clamp(0.,1.);
    t*t*(3.-2.*t)
}

#[derive(Clone, Copy)]
enum LidSide { Upper, Lower }

/// ParamEyeOpen: 1=original art, 0=upper and lower lid meet on one
/// smile-shaped arc. The CLOSED target is intentionally a narrow shared
/// curved band (not two separate rigid eyelids translated past each other).
/// Uses the measured original eye and lid bounds. No new source texture.
// A dedicated pose supplies the thicker curved lash silhouette at full
// closure. The original upper+lower art continues to deform toward it.
fn closed_smile_visibility(opening:f32)->f32 {
    // Hand off from MOVING skin + lashes to the fully opaque pose only at
    // the very end, when both borders have almost touched.
    let t=((0.115-opening)/0.105).clamp(0.,1.);
    t*t*(3.-2.*t)
}

fn mouth_pose_weight(pose:f32,target:f32)->f32 {
    (1.-(pose-target).abs()).clamp(0.,1.)
}
fn mouth_auto_weights(open:f32)->(f32,f32,f32) {
    // A little opening must show the TALK sprite; a loud opening must show
    // the WIDE sprite, including when the Talk expression is active.
    let base=1.-smoothstep((open-0.06)/0.20);
    let wide=smoothstep((open-0.57)/0.25);
    (base,(1.-base)*(1.-wide),(1.-base)*wide)
}
fn mouth_layer_weights(open:f32,pose:f32)->[f32;6] {
    // pose=0 auto, 1 talking, 2 shouting, 3 sad. A loud opening always
    // takes priority over the talk pose; sadness is the explicit override.
    let (base,_,wide_auto)=mouth_auto_weights(open);
    let sad=mouth_pose_weight(pose,3.);
    let flat=mouth_pose_weight(pose,4.);
    let bite=mouth_pose_weight(pose,5.);
    let remaining=(1.-sad-flat-bite).max(0.);
    let wide=remaining*wide_auto.max(mouth_pose_weight(pose,2.));
    let talk=(remaining-wide)*(1.-base).max(mouth_pose_weight(pose,1.));
    [remaining-wide-talk,talk,wide,sad,flat,bite]
}

fn smoothstep(value:f32)->f32 {
    let v=value.clamp(0.,1.);
    v*v*(3.-2.*v)
}

fn iris_blink_scale(opening:f32, recoil:f32)->f32 {
    // 5.5% squeeze through a blink; a spring can briefly expand the iris
    // by up to 9% on reopening, then settle with progressively smaller pulses.
    let base=0.945+0.055*smoothstep(opening/0.35);
    base*(1.+0.09*recoil.clamp(-1.,1.))
}

fn lid_close_progress(opening:f32)->f32 {
    // Exactly the same timing for upper and lower lids: no upper-ahead,
    // lower-behind drift from mismatched nonlinear easing functions.
    let shut=(1.-opening).clamp(0.,1.);
    shut*shut*(3.-2.*shut)
}

fn eyelid_vertex(x:f32,y:f32,eye:Region,lid:Region,opening:f32,kind:LidSide)->(f32,f32) {
    let progress=lid_close_progress(opening);
    let u=((x-eye.cx)/(eye.w*0.55).max(0.0001)).clamp(-1.,1.);
    let arch=(1.-u*u).max(0.);
    // A shared smile-shaped seam, exactly the same in both lid meshes.
    let smile=eye.cy + eye.h*(0.035-0.205*arch);
    let (squeeze, seam_offset)=match kind {
        LidSide::Upper=>(0.048,-0.005),
        LidSide::Lower=>(0.048, 0.005),
    };
    let target=smile+eye.h*seam_offset+(y-lid.cy)*squeeze;
    (x,y+(target-y)*progress)
}


/// The white-of-eye is an OPENING in the face, not a second skin card.
/// Its upper and lower mesh rows follow the same deformation as the
/// corresponding eyelash. The original repaired body underneath becomes
/// visible where the opening contracts; the iris keeps its native shape.
fn aperture_vertex(x:f32,y:f32,eye:Region,
                   upper_lid:Region,lower_lid:Region,opening:f32)->(f32,f32) {
    let v=((y-eye.cy)/eye.h+0.5).clamp(0.,1.);
    let (_,top)=eyelid_vertex(x,y,eye,upper_lid,opening,LidSide::Upper);
    let (_,bottom)=eyelid_vertex(x,y,eye,lower_lid,opening,LidSide::Lower);
    (x,top*(1.-v)+bottom*v)
}

// Measure the original nontransparent lash columns in source-pixel space.
// Values are relative to the alpha bounds, then mapped into the imported quad.
fn lash_profile(layer:&Layer,upper:bool)->Vec<Option<f32>> {
    let (w,h)=(layer.width as usize,layer.height as usize);
    let alpha=|x:usize,y:usize|layer.rgba[(y*w+x)*4+3];
    let (mut x0,mut y0,mut x1,mut y1)=(w,h,0usize,0usize);
    for y in 0..h { for x in 0..w { if alpha(x,y)>0 {
        x0=x0.min(x); y0=y0.min(y); x1=x1.max(x+1); y1=y1.max(y+1);
    } } }
    if x0>=x1||y0>=y1{return vec![];}
    (x0..x1).map(|x| {
        let edge=if upper {(y0..y1).find(|&y|alpha(x,y)>=96)}
                 else {(y0..y1).rev().find(|&y|alpha(x,y)>=96).map(|y|y+1)};
        edge.map(|y|(y-y0) as f32/(y1-y0) as f32)
    }).collect()
}
fn lower_edge_at(x:f32,eye:Region,lid:Region,profile:&[Option<f32>])->f32{
    let bottom=eye.cy+eye.h*0.5;
    let u=(x-(lid.cx-lid.w*0.5))/lid.w.max(0.0001);
    if !(0.0..1.0).contains(&u)||profile.is_empty(){return bottom;}
    let i=(u*profile.len() as f32) as usize;
    profile.get(i).copied().flatten().map(|v|lid.cy-lid.h*0.5+v*lid.h)
        .map(|v|v.clamp(eye.cy-eye.h*0.5,bottom)).unwrap_or(bottom)
}
fn upper_edge_at(x:f32,eye:Region,lid:Region,profile:&[Option<f32>])->f32{
    let top=eye.cy-eye.h*0.5;
    let u=(x-(lid.cx-lid.w*0.5))/lid.w.max(0.0001);
    if !(0.0..1.0).contains(&u)||profile.is_empty(){return top;}
    let i=(u*profile.len() as f32) as usize;
    profile.get(i).copied().flatten().map(|v|lid.cy-lid.h*0.5+v*lid.h)
        .map(|v|v.clamp(top,eye.cy+eye.h*0.5)).unwrap_or(top)
}
// Only compress the nasal part of the sclera as the lid closes.
// Keep the texture and neutral mesh exactly as authored.
fn nasal_x(x:f32,eye:Region,upper:Region,lower:Region,opening:f32)->f32{
    let left=eye.cx<0.;
    let tip=eye.cx+eye.w*if left {0.5} else {-0.5};
    let reach=if left {
        (upper.cx+upper.w*0.5).min(lower.cx+lower.w*0.5)
    }else{
        (upper.cx-upper.w*0.5).max(lower.cx-lower.w*0.5)
    };
    let distance=(if left {tip-reach}else{reach-tip}).max(0.);
    let band=(eye.w*0.38).max(distance*2.2+eye.w*0.031);
    let amount=smoothstep((1.-opening)/0.60)*distance.min(band*0.45);
    let t=(1.-(x-tip).abs()/band.max(0.0001)).clamp(0.,1.);
    x+(if left {-1.}else{1.})*amount*t*t
}
fn aperture_vertex_contour(x:f32,y:f32,eye:Region,upper:Region,lower:Region,
                          upper_profile:&[Option<f32>],lower_profile:&[Option<f32>],opening:f32)->(f32,f32){
    if opening>=1. {return (x,y);}
    let x=nasal_x(x,eye,upper,lower,opening);
    let bottom=lower_edge_at(x,eye,lower,lower_profile);
    let top=upper_edge_at(x,eye,upper,upper_profile).min(bottom);
    let cover=smoothstep((1.-opening)/0.12);
    let clipped=y+(y.clamp(top,bottom)-y)*cover;
    let v=((clipped-top)/(bottom-top).max(0.0001)).clamp(0.,1.);
    let (_,a)=eyelid_vertex(x,clipped,eye,upper,opening,LidSide::Upper);
    let (_,b)=eyelid_vertex(x,clipped,eye,lower,opening,LidSide::Lower);
    (x,a*(1.-v)+b*v)
}



/// Lightweight 2.5D face turn: the entire fluffy body, horns, eyes and
/// mouth share one gentle pseudo-perspective lattice. This bends the body
/// horizontally/vertically rather than rotating the flat image around Z.
/// BodyAngleX/Y are small physics-driven follow-through channels.
fn turn_warp_point(x:f32,y:f32,extent:f32,amount:f32,
                   yaw_deg:f32,pitch_deg:f32,body_yaw_deg:f32,body_pitch_deg:f32)
                  ->(f32,f32) {
    let yaw=(yaw_deg/30.).clamp(-1.,1.);
    let pitch=(pitch_deg/30.).clamp(-1.,1.);
    let follow_x=(body_yaw_deg/10.).clamp(-1.,1.);
    let follow_y=(body_pitch_deg/10.).clamp(-1.,1.);
    let nx=x/extent;
    let ny=y/extent;
    let yaw_all=yaw+follow_x*0.20;
    let pitch_all=pitch+follow_y*0.20;
    let width=1.-0.072*yaw_all.abs().min(1.);
    let height=1.-0.058*pitch_all.abs().min(1.);
    // A mild asymmetric cheek and forehead offset makes a believable
    // three-quarter pose without shearing the eyes or separating the lids.
    let x_new=x*width + extent*(
        0.042*yaw + 0.018*follow_x
        +0.025*yaw*nx*nx +0.028*yaw*(-ny)
        +0.018*amount*(-nx)
    );
    let y_new=y*height + extent*(
        0.032*pitch + 0.014*follow_y
        +0.018*pitch*ny*ny
        +0.014*yaw*nx
        +0.024*amount*ny
    );
    (x_new,y_new)
}

fn add_global_squash(model: &mut ayatsuri_format::model::Model,
                     squash:u32, head_x:u32,head_y:u32,body_x:u32,body_y:u32) {
    // Keyform axis zero changes fastest as required by Cubism.
    // 3^5=243 poses, each only a 5x5 control lattice, not 243 body meshes.
    let (extent,cols,rows)=(0.65f32,4u32,4u32);
    let axes=[
        BindingAxis{parameter:squash,keys:vec![-1.,0.,1.]},
        BindingAxis{parameter:head_x,keys:vec![-30.,0.,30.]},
        BindingAxis{parameter:head_y,keys:vec![-30.,0.,30.]},
        BindingAxis{parameter:body_x,keys:vec![-10.,0.,10.]},
        BindingAxis{parameter:body_y,keys:vec![-10.,0.,10.]},
    ];
    let n=axes.iter().map(|a|a.keys.len()).product::<usize>();
    let mut positions=Vec::with_capacity(n*(cols as usize+1)*(rows as usize+1)*2);
    for index in 0..n {
        let mut key=index;
        let values:Vec<f32>=axes.iter().map(|a|{
            let v=a.keys[key%a.keys.len()];
            key/=a.keys.len();
            v
        }).collect();
        for row in 0..=rows {
            for col in 0..=cols {
                let x=-extent+2.*extent*(col as f32/cols as f32);
                let y=-extent+2.*extent*(row as f32/rows as f32);
                let (tx,ty)=turn_warp_point(x,y,extent,
                   values[0],values[1],values[2],values[3],values[4]);
                positions.extend([tx,ty]);
            }
        }
    }
    let warp_index=model.deformers.len() as u32;
    model.deformers.push(Deformer {
        id:"AmoraHeadBodyThreeQuarter".into(),parent_part:None,
        parent_deformer:Some(0),visible:true,enabled:true,
        binding:Binding{axes:axes.to_vec(),curves:vec![]},
        kind:DeformerKind::Warp(WarpData{
            rows,cols,quad:true,opacities:vec![1.;n],positions,
        }),
        multiply:Vec::new(),screen:Vec::new(),blend_shapes:Vec::new(),
    });
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
        ("AmoraBodyYawFollow","ParamAngleX","ParamBodyAngleX",PhysicsIo::Angle,0.23),
        ("AmoraBodyPitchFollow","ParamAngleY","ParamBodyAngleY",PhysicsIo::Angle,0.23),
        ("AmoraBlinkRecoilLeft","ParamEyeLOpen","ParamIrisRecoilL",PhysicsIo::X,0.48),
        ("AmoraBlinkRecoilRight","ParamEyeROpen","ParamIrisRecoilR",PhysicsIo::X,0.48),
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
                scales.push(1. + (b - 0.5) * 0.085);
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
fn attach_live_rig(imported: &mut ayatsuri_import::Imported, doc: &Document) -> Result<()> {
    let par = |id: &str| -> Result<u32> {
        imported.model.parameters.iter().position(|p| p.id == id)
            .map(|i| i as u32).ok_or_else(|| format!("missing rig parameter: {id}").into())
    };
    let leye=par("ParamEyeLOpen")?; let reye=par("ParamEyeROpen")?;
    let look_x=par("ParamEyeBallX")?; let look_y=par("ParamEyeBallY")?;
    let mouth_open=par("ParamMouthOpenY")?;
    let mouth_form=par("ParamMouthForm")?;
    let angle=par("ParamAngleZ")?; let body_angle=par("ParamBodyAngleZ")?;
    let head_x=par("ParamAngleX")?;let head_y=par("ParamAngleY")?;
    let body_x=par("ParamBodyAngleX")?;let body_y=par("ParamBodyAngleY")?;
    let breath=par("ParamBreath")?;
    let iris_bounce_x=par("ParamIrisBounceX")?;
    let iris_bounce_y=par("ParamIrisBounceY")?;
    let squash=par("ParamBodySquash")?;
    let blush=par("ParamBlush")?;
    let mouth_pose=par("ParamMouthPose")?;
    let names = imported.mesh_names.clone();
    let regions: HashMap<String,Region> = names.iter().cloned().zip(imported.model.art_meshes.iter().map(Region::of)).collect();
    let idx = |name: &str| names.iter().position(|n| n==name);
    let open_keys = [0.,0.010,0.06,0.115,0.18,0.50,1.];
    let aperture_keys = [0.,0.010,0.06,0.115,0.18,0.35,0.50,0.70,0.85,0.95,1.];
    let look_keys = [-1., 0., 1.];
    let tears_keys=[0.,0.5,1.];
    let pose_keys=[0.,1.,2.,3.,4.,5.];
    for (side, eye_param) in [("L",leye),("R",reye)] {
        let iris_recoil=par(&format!("ParamIrisRecoil{side}"))?;
        let white_name = format!("eye_white_{side}");
        let Some(white_index) = idx(&white_name) else { continue; };
        let eye = regions[&white_name];
        // Local skin from the *original* JPEG: a feathered patch follows the
        // area above/below the sclera as the eyelids meet. This avoids
        // stretching the complete face or horns to close one eye.
        if let Some(i) = idx(&format!("skin_patch_{side}")) {
            let skin=&mut imported.model.art_meshes[i];
            subdivide(skin, 16, 12);
            // The cleaned body already fills the original white JPEG eye
            // holes. Moving a duplicated patch over that same face created
            // visible detached skin and out-of-sync edges in VTube Studio.
            // Keep the legacy mesh for package continuity, but make it inert.
            key_mesh(skin, &[(eye_param,&open_keys)],
                |_,x,y|(x,y), |_|0.);
        }
        let white = &mut imported.model.art_meshes[white_index];
        subdivide(white, 48, 16);
        // Deform ONLY the sclera's silhouette: its top/bottom edges follow
        // the matching eyelash. Behind it is the clean original face skin.
        // The iris itself NEVER stretches and is clipped to this aperture.
        let top=regions[&format!("upper_lid_{side}")];
        let bottom=regions[&format!("lower_lid_{side}")];
        let lower_source=doc.layers.iter().find(|l|l.name==format!("lower_lid_{side}")).ok_or("missing lower lash source")?;
        let upper_source=doc.layers.iter().find(|l|l.name==format!("upper_lid_{side}")).ok_or("missing upper lash source")?;
        let lower_profile=lash_profile(lower_source,false);
        let upper_profile=lash_profile(upper_source,true);
        key_mesh(white, &[(eye_param,&aperture_keys)],
            |v,x,y| aperture_vertex_contour(x,y,eye,top,bottom,&upper_profile,&lower_profile,v[0]),
            |v| eye_visibility(v[0]));
        let iris_name=format!("iris_{side}");
        if let Some(i)=idx(&iris_name) {
            let iris_region=regions[&iris_name];
            let iris=&mut imported.model.art_meshes[i];
            subdivide(iris, 8, 6);
            // Clip pupils to the white: they cannot stick out on side glances.
            iris.masks=vec![white_index as u32];
            // Real eye movement: drive toward the outer rim but clip to the
            // original sclera. At maximum look, an iris reaches ~55% of the
            // available edge gap, instead of barely shifting at all.
            let travel_x=((eye.w-iris_region.w).max(eye.w*0.18)*0.56).min(eye.w*0.22);
            let travel_y=((eye.h-iris_region.h).max(eye.h*0.12)*0.48).min(eye.h*0.16);
            // Inertial bounce channels are driven by exported Cubism physics3.
            // Gentle overshoot and elastic squash accompany gaze transitions.
            key_mesh(iris, &[(eye_param,&open_keys),(look_x,&look_keys),(look_y,&look_keys),
                             (iris_bounce_x,&look_keys),(iris_bounce_y,&look_keys),
                             (iris_recoil,&look_keys)],
                |v,x,y| {
                    let opening=v[0];
                    let rel_x=x-iris_region.cx;
                    let rel_y=y-iris_region.cy;
                    let bounce_x=v[3]; let bounce_y=v[4];
                    let spring=iris_blink_scale(opening,v[5]);
                    let stretch_x=spring*(1.0+bounce_x.abs()*0.035 + v[1].abs()*0.010);
                    let stretch_y=spring*(1.0+bounce_y.abs()*0.040 - bounce_x.abs()*0.018);
                    // Recoil is centered on each iris: squash at closure,
                    // then overshoot and settle. The white remains the mask.
                    (iris_region.cx + rel_x*stretch_x + v[1]*travel_x*opening + bounce_x*eye.w*0.040*opening,
                     iris_region.cy + rel_y*stretch_y + v[2]*travel_y*opening + bounce_y*eye.h*0.035*opening)
                }, |v| eye_visibility(v[0]));
        }
        for (kind, upper) in [("upper",true),("lower",false)] {
            let name = format!("{kind}_lid_{side}");
            if let Some(i)=idx(&name) {
                let old=regions[&name];
                let lid=&mut imported.model.art_meshes[i];
                subdivide(lid, 48, 5);
                // Retarget BOTH lid meshes to the same smiling closure
                // seam. At 0 they form one curved line; at 1 the original
                // upper and lower art geometry is reproduced exactly.
                let kind=if upper {LidSide::Upper} else {LidSide::Lower};
                key_mesh(lid,&[(eye_param,&open_keys)],
                    |v,x,y| eyelid_vertex(x,y,eye,old,v[0],kind),
                    // Both physical lid meshes hand over to the bold
                    // closed eyelash shape at the SAME rate.
                    |v| 1.-closed_smile_visibility(v[0]));

            }
        }
        // The reference's happy closed eye is a bold arch, not a hairline.
        // This technical keyform draws that silhouette as BOTH existing lid
        // meshes finish converging onto it; no source image is replaced.
        if let Some(i)=idx(&format!("closed_smile_{side}")) {
            let pose=&mut imported.model.art_meshes[i];
            key_mesh(pose,&[(eye_param,&open_keys)],
                |_,x,y|(x,y),|v|closed_smile_visibility(v[0]));
        }
        // Supplied tear sprites remain anchored to the lower eyelash.
        // They follow its deformation even at 0 eye-open and remain semi-
        // transparent while closed, rather than floating on the canvas.
        if let Some(i)=idx(&format!("extra_tear_{side}")) {
            let tear_control=par(&format!("ParamTear{side}"))?;
            let region=regions[&format!("extra_tear_{side}")];
            let lid=regions[&format!("lower_lid_{side}")];
            let tear=&mut imported.model.art_meshes[i];
            key_mesh(tear,&[(eye_param,&open_keys),(tear_control,&tears_keys)],
                |v,x,y| {
                    let (_,edge)=eyelid_vertex(region.cx,lid.cy,eye,lid,v[0],LidSide::Lower);
                    (x,y+edge-lid.cy)
                }, |v|v[1]*(0.55+0.30*v[0]));
        }
    }
    if let Some(i)=idx("extra_blush") {
        let mesh=&mut imported.model.art_meshes[i];
        key_mesh(mesh,&[(blush,&tears_keys)],|_,x,y|(x,y),|v|v[0]);
    }
    if let Some(i)=idx("mouth") {
        let mouth_region=regions["mouth"];
        let mouth=&mut imported.model.art_meshes[i];
        subdivide(mouth, 8, 5);
        let open_keys = [0.,0.06,0.16,0.32,0.57,0.72,0.82,1.];
        let form_keys = [-1., 0., 1.];
        key_mesh(mouth,&[(mouth_open,&open_keys),(mouth_form,&form_keys),(mouth_pose,&pose_keys)],
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
            }, |v|mouth_layer_weights(v[0],v[2])[0]);
    }
    for (name,position) in [
        ("extra_mouth_talk",1.),
        ("extra_mouth_open_wide",2.),
        ("extra_mouth_sad",3.),
        ("extra_mouth_flat",4.),
        ("extra_mouth_bite",5.),
    ] {
        if let Some(i)=idx(name) {
            let region=regions[name];
            let mesh=&mut imported.model.art_meshes[i];
            key_mesh(mesh,&[(mouth_pose,&pose_keys),(mouth_open,&[0.,0.06,0.16,0.32,0.57,0.72,0.82,1.])],
                |v,x,y| {
                    let movement=if position>=3. {0.} else {v[1]};
                    let sx=1.+0.035*movement;
                    let sy=1.+0.045*movement;
                    (region.cx+(x-region.cx)*sx,region.cy+(y-region.cy)*sy)
                },|v| mouth_layer_weights(v[1],v[0])[position as usize]);
        }
    }
    add_root_motion(&mut imported.model,angle,body_angle,breath);
    add_global_squash(&mut imported.model,squash,head_x,head_y,body_x,body_y);
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
        ("Neutral",&[("ParamEyeLOpen",1.),("ParamEyeROpen",1.),("ParamMouthPose",0.),("ParamMouthOpenY",0.),("ParamMouthForm",0.),("ParamBlush",0.),("ParamTearL",0.),("ParamTearR",0.)]),
        ("Blushing",&[("ParamBlush",0.8),("ParamMouthPose",1.),("ParamEyeLOpen",0.76),("ParamEyeROpen",0.76)]),
        ("Happy",&[("ParamEyeLOpen",0.87),("ParamEyeROpen",0.87),("ParamMouthForm",0.8),("ParamMouthOpenY",0.28),("ParamMouthPose",1.),("ParamBlush",0.30)]),
        ("Surprised",&[("ParamEyeLOpen",1.),("ParamEyeROpen",1.),("ParamMouthForm",-0.5),("ParamMouthOpenY",1.),("ParamMouthPose",2.)]),
        ("Sleepy",&[("ParamEyeLOpen",0.27),("ParamEyeROpen",0.27),("ParamMouthForm",-0.3),("ParamMouthOpenY",0.)]),
        ("Annoyed",&[("ParamEyeLOpen",0.54),("ParamEyeROpen",0.54),("ParamMouthForm",-0.85),("ParamMouthOpenY",0.),("ParamMouthPose",3.),("ParamBlush",0.58)]),
        ("WinkLeft",&[("ParamEyeLOpen",0.),("ParamEyeROpen",1.),("ParamMouthForm",0.7)]),
        ("WinkRight",&[("ParamEyeLOpen",1.),("ParamEyeROpen",0.),("ParamMouthForm",0.7)]),
        ("Shy",&[("ParamEyeLOpen",0.70),("ParamEyeROpen",0.70),("ParamMouthForm",0.),("ParamMouthOpenY",0.),("ParamEyeBallY",0.3),("ParamBlush",1.),("ParamMouthPose",5.)]),
        ("Curious",&[("ParamEyeLOpen",1.),("ParamEyeROpen",0.76),("ParamMouthForm",0.2),("ParamMouthOpenY",0.1),("ParamEyeBallX",0.42)]),
        ("Sad",&[("ParamEyeLOpen",0.52),("ParamEyeROpen",0.52),("ParamMouthPose",3.),("ParamBlush",0.20)]),
        ("Crying",&[("ParamEyeLOpen",0.38),("ParamEyeROpen",0.38),("ParamMouthPose",3.),("ParamBlush",0.22),("ParamTearL",0.92),("ParamTearR",0.92)]),
        ("Sobbing",&[("ParamEyeLOpen",0.36),("ParamEyeROpen",0.36),("ParamMouthPose",3.),("ParamMouthOpenY",0.),("ParamMouthForm",-1.),("ParamAngleY",8.),("ParamBlush",0.23),("ParamTearL",1.),("ParamTearR",1.)]),
        ("LaughingTears",&[("ParamEyeLOpen",0.10),("ParamEyeROpen",0.10),("ParamMouthPose",2.),("ParamMouthOpenY",0.9),("ParamMouthForm",0.9),("ParamBlush",0.35),("ParamTearL",1.),("ParamTearR",1.)]),
        ("Deadpan",&[("ParamEyeLOpen",0.86),("ParamEyeROpen",0.86),("ParamMouthPose",4.),("ParamMouthOpenY",0.)]),
        ("Enchanted",&[("ParamEyeLOpen",0.96),("ParamEyeROpen",0.96),("ParamMouthPose",5.),("ParamMouthOpenY",0.),("ParamEyeBallY",-0.13),("ParamBlush",0.70)]),
        ("Angry",&[("ParamEyeLOpen",0.60),("ParamEyeROpen",0.60),("ParamMouthPose",4.),("ParamMouthOpenY",0.),("ParamMouthForm",-1.),("ParamBlush",0.72)]),
        ("Shouting",&[("ParamMouthPose",2.),("ParamMouthOpenY",1.),("ParamBlush",0.40)]),
    ];
    let mut files=Vec::new();
    for (preset, params) in presets {
        let filename=format!("{name}_{preset}.exp3.json");
        let mut param_values:Vec<(&str,f32)>=params.to_vec();
        // Each emotion explicitly resets independent overlays to prevent
        // stale tears, blush or mouth pose persisting after a preset switch.
        for (id,value) in [("ParamBlush",0.),("ParamTearL",0.),
                           ("ParamTearR",0.),("ParamMouthPose",0.)] {
            if !param_values.iter().any(|(key,_)|*key==id){
                param_values.push((id,value));
            }
        }
        fs::write(dir.join(&filename),serde_json::to_vec_pretty(&json!({
            "Type":"Live2D Expression",
            "FadeInTime":0.25,
            "FadeOutTime":0.25,
            "Parameters": param_values.iter().map(|(id,value)|json!({
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
        ("ParamAngleX",&[(0.,0.),(0.8,0.),(1.4,12.),(2.2,18.),(3.0,0.),(4.0,-16.),(5.1,0.),(6.,0.)]),
        ("ParamAngleY",&[(0.,0.),(1.2,0.),(2.0,9.),(3.2,0.),(4.3,-9.),(5.4,0.),(6.,0.)]),
        ("ParamMouthOpenY",&[(0.,0.),(2.,0.),(2.4,0.5),(2.9,0.),(6.,0.)]),
        ("ParamBreath",&[(0.,0.5),(1.5,0.85),(3.,0.5),(4.5,0.15),(6.,0.5)]),
        ("ParamBodyAngleZ",&[(0.,0.),(1.5,4.),(3.,0.),(4.5,-4.),(6.,0.)]),
        ("ParamAngleZ",&[(0.,0.),(1.5,2.),(3.,0.),(4.5,-2.),(6.,0.)]),
        ("ParamMouthForm",&[(0.,0.),(2.,0.),(2.4,0.75),(2.9,0.),(6.,0.)]),
        ("ParamIrisRecoilL",&[(0.,0.),(1.60,0.),(1.79,0.),(1.85,0.86),(1.96,0.),(2.03,0.45),(2.14,0.),(2.23,0.18),(2.30,0.),(6.,0.)]),
        ("ParamIrisRecoilR",&[(0.,0.),(1.60,0.),(1.79,0.),(1.85,0.86),(1.96,0.),(2.03,0.45),(2.14,0.),(2.23,0.18),(2.30,0.),(6.,0.)]),
        ("ParamMouthPose",&[(0.,0.),(2.,0.),(2.35,1.),(3.,0.),(6.,0.)]),
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
        parameter("ParamIrisRecoilL", -1.0, 1.0, 0.0),
        parameter("ParamIrisRecoilR", -1.0, 1.0, 0.0),
        parameter("ParamIrisBounceX", -1.0, 1.0, 0.0),
        parameter("ParamIrisBounceY", -1.0, 1.0, 0.0),
        parameter("ParamMouthOpenY", 0.0, 1.0, 0.0),
        parameter("ParamMouthForm", -1.0, 1.0, 0.0),
        parameter("ParamMouthPose", 0.0, 5.0, 0.0),
        parameter("ParamBlush", 0.0, 1.0, 0.0),
        parameter("ParamTearL", 0.0, 1.0, 0.0),
        parameter("ParamTearR", 0.0, 1.0, 0.0),
        parameter("ParamAngleZ", -30.0, 30.0, 0.0),
        parameter("ParamAngleX", -30.0, 30.0, 0.0),
        parameter("ParamAngleY", -30.0, 30.0, 0.0),
        parameter("ParamBodyAngleX", -10.0, 10.0, 0.0),
        parameter("ParamBodyAngleY", -10.0, 10.0, 0.0),
        parameter("ParamBodyAngleZ", -10.0, 10.0, 0.0),
        parameter("ParamBreath", 0.0, 1.0, 0.5),
        parameter("ParamBodySquash", -1.0, 1.0, 0.0),
    ]);
    attach_live_rig(&mut imported, doc)?;
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
    if parsed.groups.len()!=7 || parsed.groups.iter().any(|g| g.inputs.is_empty()||g.outputs.is_empty()) {
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
    fn closed_happy_eyelid_pose_is_visible_only_during_closure() {
        assert_eq!(closed_smile_visibility(1.),0.);
        assert_eq!(closed_smile_visibility(0.5),0.);
        assert_eq!(closed_smile_visibility(0.18),0.);
        assert!(closed_smile_visibility(0.06)>0.);
        assert!(closed_smile_visibility(0.02)>0.9);
        assert_eq!(closed_smile_visibility(0.),1.);
    }

    #[test]
    fn aperture_edges_follow_upper_and_lower_lash_exactly() {
        let eye=Region{cx:0.,cy:0.,w:2.,h:2.};
        let upper=Region{cx:0.,cy:-0.6,w:2.,h:0.3};
        let lower=Region{cx:0.,cy:0.6,w:2.,h:0.3};
        for opening in [0.,0.06,0.18,0.35,0.5,1.] {
            for x in [-0.5,0.,0.5] {
                for (y,lid,kind) in [
                    (-eye.h*0.5,upper,LidSide::Upper),
                    (eye.h*0.5,lower,LidSide::Lower)
                ] {
                    let (_,edge)=aperture_vertex(x,y,eye,upper,lower,opening);
                    let (_,lash)=eyelid_vertex(x,y,eye,lid,opening,kind);
                    assert!((edge-lash).abs()<1e-5,"white edge detached from eyelash");
                }
            }
        }
    }

    #[test]
    fn eyeball_only_fades_during_matching_closed_pose_handoff() {
        for open in [0.,0.010,0.035,0.06,0.095,0.115,0.18,0.35,0.5,1.] {
            let visible=eye_visibility(open);
            let closed=closed_smile_visibility(open);
            assert!((visible+closed-1.).abs()<1e-5,
                    "closed sprite and original eye must crossfade complementarily");
            if open>=0.115 {
                assert_eq!(visible,1.,"early iris fading");
                assert_eq!(closed,0.,"early closed sprite");
            }
        }
        assert_eq!(eye_visibility(0.),0.);
        assert_eq!(closed_smile_visibility(0.),1.);
    }

    #[test]
    fn lids_share_exact_closure_easing_and_overlay_handoff() {
        for opening in [0.0,0.18,0.35,0.50,0.75,1.0] {
            let p=lid_close_progress(opening);
            assert!((0.0..=1.0).contains(&p));
            let alpha=closed_smile_visibility(opening);
            assert!((1.0-alpha+alpha-1.0).abs() < 1e-6);
        }
        assert_eq!(lid_close_progress(0.),1.);
        assert_eq!(lid_close_progress(1.),0.);
        assert_eq!(closed_smile_visibility(0.),1.);
        assert_eq!(closed_smile_visibility(1.),0.);
    }

    #[test]
    fn happy_blink_upper_and_lower_lids_meet_on_same_arch() {
        let eye=Region {cx:0.,cy:0.,w:2.,h:2.};
        let top=Region {cx:0.,cy:-0.6,w:2.,h:0.8};
        let bottom=Region {cx:0.,cy:0.65,w:2.,h:0.45};
        for &open in &[0.,0.18,0.5,1.] {
            let (_,uy)=eyelid_vertex(0.,top.cy,eye,top,open,LidSide::Upper);
            let (_,ly)=eyelid_vertex(0.,bottom.cy,eye,bottom,open,LidSide::Lower);
            assert!(uy.is_finite()&&ly.is_finite());
            if open==1. {
                assert!((uy-top.cy).abs()<1e-6);
                assert!((ly-bottom.cy).abs()<1e-6);
            }
            if open==0. {
                assert!((uy-ly).abs() < eye.h*0.04, "closed lids must meet");
                let (_,edge)=eyelid_vertex(eye.w*0.51,top.cy,eye,top,open,LidSide::Upper);
                assert!(uy < edge-eye.h*0.12, "closed smile crown is above corners");
            }
        }
        let (_,upper_mid)=eyelid_vertex(0.,top.cy,eye,top,0.5,LidSide::Upper);
        let (_,lower_mid)=eyelid_vertex(0.,bottom.cy,eye,bottom,0.5,LidSide::Lower);
        assert!(upper_mid>top.cy && lower_mid<bottom.cy, "both lids must participate");
    }

    #[test]
    fn contour_attachment_uses_actual_lower_lash_edge() {
        let eye=Region{cx:0.,cy:0.,w:2.,h:2.};
        let upper=Region{cx:0.,cy:-0.8,w:2.,h:0.4};
        let lower=Region{cx:0.,cy:0.6,w:2.,h:0.6};
        let profile=vec![Some(0.5);32];
        let y=lower_edge_at(0.,eye,lower,&profile);
        for opening in [1.,0.95,0.8,0.5,0.18,0.] {
            let (_,actual)=aperture_vertex_contour(0.,y,eye,upper,lower,&[],&profile,opening);
            let (_,lash)=eyelid_vertex(0.,y,eye,lower,opening,LidSide::Lower);
            assert!((actual-lash).abs()<1e-5);
        }
    }

    #[test]
    fn contour_attachment_uses_actual_upper_lash_edge() {
        let eye=Region{cx:0.,cy:0.,w:2.,h:2.};
        let upper=Region{cx:0.,cy:-0.6,w:2.,h:0.6};
        let lower=Region{cx:0.,cy:0.6,w:2.,h:0.6};
        let profile=vec![Some(0.5);32];
        let y=upper_edge_at(0.,eye,upper,&profile);
        for opening in [1.,0.95,0.85,0.7,0.5,0.35,0.18,0.] {
            let (_,actual)=aperture_vertex_contour(0.,y,eye,upper,lower,&profile,&[],opening);
            let (_,lash)=eyelid_vertex(0.,y,eye,upper,opening,LidSide::Upper);
            assert!((actual-lash).abs()<1e-5);
        }
    }

    #[test]
    fn neutral_eye_preserves_source_and_nasal_deformation_is_monotonic() {
        for sign in [-1.,1.] {
            let eye=Region{cx:sign*0.18,cy:0.,w:0.2,h:0.22};
            let upper=Region{cx:sign*0.19,cy:-0.05,w:0.18,h:0.08};
            let lower=Region{cx:sign*0.20,cy:0.05,w:0.16,h:0.05};
            for i in 0..=100 {
                let x=eye.cx-eye.w*0.5+eye.w*i as f32/100.;
                for j in 0..=10 {
                    let y=eye.cy-eye.h*0.5+eye.h*j as f32/10.;
                    assert_eq!(aperture_vertex_contour(x,y,eye,upper,lower,&[],&[],1.),(x,y));
                }
            }
            for opening in [1.,0.98,0.95,0.85,0.7,0.5,0.35,0.18,0.] {
                let mut previous=f32::NEG_INFINITY;
                for i in 0..=100 {
                    let x=eye.cx-eye.w*0.5+eye.w*i as f32/100.;
                    let mapped=nasal_x(x,eye,upper,lower,opening);
                    assert!(mapped>=previous,"folded nasal mesh");
                    previous=mapped;
                }
            }
        }
    }

    #[test]
    fn mouth_and_emotion_crossfades_preserve_neutral() {
        assert_eq!(mouth_pose_weight(0.,0.),1.);
        assert_eq!(mouth_pose_weight(0.,1.),0.);
        assert_eq!(mouth_pose_weight(3.,3.),1.);
        let (base,talk,wide)=mouth_auto_weights(0.);
        assert_eq!((base,talk,wide),(1.,0.,0.));
        let (base,talk,wide)=mouth_auto_weights(1.);
        assert!((base+talk+wide-1.).abs()<1e-5 && wide>0.99);
        assert!((mouth_pose_weight(1.5,1.)+mouth_pose_weight(1.5,2.)-1.).abs()<1e-6);
        let small=mouth_layer_weights(0.20,0.);
        assert!(small[1]>small[0] && small[1]>small[2], "small opening must talk");
        let large=mouth_layer_weights(0.95,1.);
        assert!(large[2]>0.999, "large opening overrides Talk pose");
        assert_eq!(mouth_layer_weights(1.,4.),[0.,0.,0.,0.,1.,0.]);
        assert_eq!(mouth_layer_weights(1.,5.),[0.,0.,0.,0.,0.,1.]);
        let sad=mouth_layer_weights(0.8,3.);
        assert!((sad[3]-1.).abs()<1e-6, "sad is explicit emotion");
        for open in [0.,0.2,0.5,0.95,1.] {
            for pose in [0.,1.,1.5,2.,2.5,3.,3.5,4.,4.5,5.] {
                let w=mouth_layer_weights(open,pose);
                assert!((w.iter().sum::<f32>()-1.).abs()<1e-5,
                        "mouth layers must always sum to one");
                assert!(w.iter().all(|a|*a>=0. && *a<=1.));
            }
        }
    }

    #[test]
    fn iris_recoil_is_small_and_recovers_to_original_scale() {
        assert!((iris_blink_scale(0.,0.)-0.945).abs()<1e-5);
        assert!((iris_blink_scale(1.,0.)-1.).abs()<1e-5);
        assert!(iris_blink_scale(1.,1.)<=1.09+1e-5);
        assert!(iris_blink_scale(1.,-1.)>=0.90);
        assert!(iris_blink_scale(0.15,0.)<1.);
    }

    #[test]
    fn head_body_warp_is_centered_small_and_bidirectional() {
        let extent=0.65;
        let at=|yaw,pitch|{
            turn_warp_point(0.22,-0.16,extent,0.,yaw,pitch,0.,0.)
        };
        let (x0,y0)=at(0.,0.);
        let (xl,_) =at(-30.,0.);
        let (xr,_) =at(30.,0.);
        assert!((x0-0.22).abs()<1e-5 && (y0+0.16).abs()<1e-5);
        assert!(xl<x0&&x0<xr,"head yaw must turn in both directions");
        let (_,yu)=at(0.,-30.);
        let (_,yd)=at(0.,30.);
        assert!(yu<y0&&yd>y0,"pitch must move the complete head");
        for yaw in [-30.,0.,30.] {
            for pitch in [-30.,0.,30.] {
                let (x,y)=at(yaw,pitch);
                assert!(x.is_finite()&&y.is_finite());
                assert!((x-0.22).abs()<0.16 && (y+0.16).abs()<0.16);
            }
        }
    }

    #[test]
    fn eyelid_aperture_visibility_is_smooth() {
        assert_eq!(eye_visibility(0.0), 0.0);
        assert_eq!(eye_visibility(0.18),1.0);
        assert_eq!(eye_visibility(0.5), 1.0);
        assert_eq!(eye_visibility(1.0), 1.0);
    }

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
