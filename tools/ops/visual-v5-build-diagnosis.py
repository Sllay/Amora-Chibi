import json, os, re, urllib.request, urllib.parse, urllib.error
token=os.environ.get("NORTHFLANK_API_TOKEN","")
if not token:
    print("DIAGNOSIS "+json.dumps({"status":"token_missing"}))
    raise SystemExit(0)
team=os.environ.get("NORTHFLANK_TEAM_ID","mejorberrys-team")
project=os.environ.get("NORTHFLANK_PROJECT_ID","default")
for value in (team,project):
    if not re.fullmatch(r"[A-Za-z0-9-]{1,64}",value): raise SystemExit("Invalid resource ID")
base=f"https://api.northflank.com/v1/teams/{team}/projects/{project}/services/loyal-coat"
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None
opener=urllib.request.build_opener(NoRedirect())
def get(path,query=None):
    url=base+path+("?" + urllib.parse.urlencode(query) if query else "")
    req=urllib.request.Request(url,headers={"Authorization":"Bearer "+token,"Accept":"application/json"})
    try:
        with opener.open(req,timeout=20) as r:
            raw=r.read(4_000_001)
            if len(raw)>4_000_000: return None,"response_too_large"
            return json.loads(raw).get("data"),None
    except urllib.error.HTTPError as e: return None,"HTTP_"+str(e.code)
    except Exception: return None,"read_failed"
def sha(s):
    return s[:12] if isinstance(s,str) and re.fullmatch("[a-f0-9]{40}",s) else None
def when(s):
    return s if isinstance(s,str) and re.fullmatch(r"20\d\d-\d\d-\d\dT[0-9:.+-]+Z?",s) else None
data,err=get("/build",{"per_page":5})
out={"status":"read_only","list_error":err,"builds":[]}
if isinstance(data,dict):
    for item in data.get("builds",[])[:5]:
        if not isinstance(item,dict): continue
        ident=item.get("id")
        status=item.get("status")
        status=status if status in {"QUEUED","PENDING","UNSCHEDULABLE","STARTING","CLONING","BUILDING","UPLOADING","ABORTED","FAILURE","SUBMISSION_FAILURE","SUCCESS","CRASHED","IN_PROGRESS"} else "unknown"
        row={"sha":sha(item.get("sha")),"created":when(item.get("createdAt")),"status":status,"main":item.get("branch")=="main"}
        if isinstance(ident,str) and re.fullmatch("[A-Za-z0-9-]{1,100}",ident):
            lines,log_error=get("/build-logs",{"buildId":ident,"queryType":"range","lineLimit":700,"direction":"backward"})
            row["logs_error"]=log_error
            if isinstance(lines,list):
                msg="\n".join(str(x.get("log","")) for x in lines if isinstance(x,dict))
                row["log_lines"]=len(lines)
                row["signals"]={
                    "preview_instruction": "AMORA_VISUAL_V5_BUILD" in msg,
                    "preview_command_cached": bool(re.search(r"(?:visual-preview|AMORA_VISUAL_V5_BUILD)[^\n]{0,400}\n[^\n]{0,100}CACHED",msg,re.I)),
                    "identity_generated": bool(re.search(r"BUILD_IDENTITY\s+\{",msg)),
                    "model_fetch_failed": "V5 pinned rig download/extraction failed" in msg,
                    "secret_mount_failure": bool(re.search(r"secret[^\n]*(?:not found|missing|required)|test -s /run/secrets/[^\n]*exit code",msg,re.I)),
                    "build_failed": "failed to solve" in msg or "exit code: 1" in msg,
                    "framework_fetch": bool(re.search(r"(?:Cloning into|fetching).*framework",msg,re.I)),
                    "esbuild_output": bool(re.search(r"(?:visual-preview|\.build)/(?:app|presentation)\.js\s+\d",msg)),
                    "permission_denied": "Permission denied" in msg,
                    "manifest_text": "manifest.json" in msg,
                }
        out["builds"].append(row)
dep,err=get("/deployment")
out["deployment_error"]=err
if isinstance(dep,dict):
    # Only extract a known commit SHA from documented deployment fields.
    candidates=[]
    for d in (dep,dep.get("deployment"),dep.get("internal"),dep.get("vcs")):
        if isinstance(d,dict):
            for key in ("sha","commitSha","deployedSha"):
                if sha(d.get(key)): candidates.append(sha(d[key]))
    out["deployment_shas"]=sorted(set(candidates))
print("DIAGNOSIS "+json.dumps(out,sort_keys=True))
