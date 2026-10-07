import os, re, uuid, shutil, subprocess
from pathlib import Path
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl

BASE=Path(__file__).resolve().parent
JOBS=BASE/'jobs'; JOBS.mkdir(exist_ok=True)
MODEL=os.getenv('WHISPER_MODEL','small')
app=FastAPI(title='ClipForge API')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'], allow_headers=['*'])

class ClipRequest(BaseModel):
    url: HttpUrl
    format: str='9:16 Vertical'
    quality: str='1080p'
    captions: str='Dynamic subtitles'

def run(cmd):
    p=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    if p.returncode: raise RuntimeError(p.stdout[-5000:])
    return p.stdout

def srt_time(sec):
    ms=int((sec-int(sec))*1000); sec=int(sec); h=sec//3600; m=(sec%3600)//60; s=sec%60
    return f'{h:02}:{m:02}:{s:02},{ms:03}'

def make_srt(segments, path, offset=0, end=None):
    lines=[]; n=1
    for seg in segments:
        a=max(0,seg['start']-offset); b=min((end-offset) if end else 99999,seg['end']-offset)
        if b<=0 or a>=b: continue
        a=max(0,a); b=max(a+0.2,b)
        txt=re.sub(r'\s+',' ',seg['text']).strip()
        if txt: lines += [str(n),f'{srt_time(a)} --> {srt_time(b)}',txt,'']; n+=1
    path.write_text('\n'.join(lines),encoding='utf-8')

def process(jobid, req):
    d=JOBS/jobid; d.mkdir(exist_ok=True); status={'state':'processing','progress':5,'message':'Downloading authorized source…'}
    (d/'status.json').write_text(__import__('json').dumps(status))
    try:
        # yt-dlp is intentionally server-side; use only with content the user is authorized to process.
        run(['yt-dlp','--no-playlist','-f','bv*[height<=1080]+ba/b[height<=1080]','--merge-output-format','mp4','-o',str(d/'source.%(ext)s'),str(req.url)])
        source=next(d.glob('source.*'))
        status.update(progress=25,message='Transcribing audio…'); (d/'status.json').write_text(__import__('json').dumps(status))
        from faster_whisper import WhisperModel
        wm=WhisperModel(MODEL,device=os.getenv('WHISPER_DEVICE','cpu'),compute_type=os.getenv('WHISPER_COMPUTE_TYPE','int8'))
        segs,info=wm.transcribe(str(source),vad_filter=True,word_timestamps=False)
        segs=[{'start':float(x.start),'end':float(x.end),'text':x.text.strip()} for x in segs]
        duration=float(info.duration)
        # Choose up to three transcript windows, favoring substantive segments spaced across the video.
        candidates=[]
        for s in segs:
            if 12 <= (s['end']-s['start']) <= 90 and len(s['text'])>45:
                candidates.append(s)
        if not candidates: candidates=segs[:]
        chosen=[]
        for s in candidates:
            start=max(0,s['start']-2); end=min(duration,start+60)
            if end-start<8: continue
            if all(abs(start-c[0])>45 for c in chosen): chosen.append((start,end))
            if len(chosen)>=3: break
        if not chosen: chosen=[(0,min(60,duration))]
        status.update(progress=45,message='Rendering vertical clips…'); (d/'status.json').write_text(__import__('json').dumps(status))
        out=[]
        for i,(start,end) in enumerate(chosen,1):
            srt=d/f'clip{i}.srt'; make_srt(segs,srt,start,end)
            mp4=d/f'clip-{i}.mp4'
            vf='scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920'
            if req.captions!='Off': vf += ",subtitles='"+str(srt).replace("'","\\'")+"'"
            run(['ffmpeg','-y','-ss',str(start),'-to',str(end),'-i',str(source),'-vf',vf,'-c:v','libx264','-preset','veryfast','-crf','20','-c:a','aac','-b:a','128k',str(mp4)])
            out.append(mp4.name)
            status.update(progress=45+int(50*i/len(chosen)),message=f'Rendered clip {i} of {len(chosen)}'); (d/'status.json').write_text(__import__('json').dumps(status))
        status={'state':'done','progress':100,'message':'Your clips are ready.','clips':out}; (d/'status.json').write_text(__import__('json').dumps(status))
    except Exception as e:
        status={'state':'error','progress':100,'message':str(e)[:800]}; (d/'status.json').write_text(__import__('json').dumps(status))

@app.post('/api/jobs')
def create(req:ClipRequest,bg:BackgroundTasks):
    jobid=uuid.uuid4().hex
    (JOBS/jobid/'status.json').parent.mkdir(parents=True,exist_ok=True)
    (JOBS/jobid/'status.json').write_text(__import__('json').dumps({'state':'queued','progress':0,'message':'Queued…'}))
    bg.add_task(process,jobid,req)
    return {'job_id':jobid}

@app.get('/api/jobs/{jobid}')
def status(jobid:str):
    import json
    p=JOBS/jobid/'status.json'
    if not p.exists(): raise HTTPException(404,'Job not found')
    data=json.loads(p.read_text())
    if 'clips' in data: data['clips']=[f'/api/jobs/{jobid}/files/{x}' for x in data['clips']]
    return data

@app.get('/api/jobs/{jobid}/files/{filename}')
def file(jobid:str,filename:str):
    from fastapi.responses import FileResponse
    p=(JOBS/jobid/filename).resolve()
    if not str(p).startswith(str((JOBS/jobid).resolve())) or not p.exists(): raise HTTPException(404,'File not found')
    return FileResponse(p,media_type='video/mp4',filename=filename)

from fastapi.responses import HTMLResponse

INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ClipForge — AI Video Clipper & YouTube Shorts Maker</title>
<meta name="description" content="ClipForge turns authorized long-form videos into vertical Shorts with automatic subtitles. AI video clipper for creators.">
<meta name="robots" content="index,follow">
<link rel="canonical" href="https://YOUR-DOMAIN.example/">
<style>
:root{--bg:#07080d;--panel:#10121a;--line:#242836;--text:#f7f8fb;--muted:#969bab;--a:#8b5cf6;--b:#ec4899;--ok:#34d399}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 20% 0,#21103e 0,transparent 32%),radial-gradient(circle at 90% 10%,#3a102c 0,transparent 25%),var(--bg);color:var(--text);font:15px system-ui,-apple-system,Segoe UI,Roboto,sans-serif}.wrap{width:min(1080px,92%);margin:auto}
nav{display:flex;justify-content:space-between;align-items:center;padding:22px 0}.logo{font-size:22px;font-weight:900}.logo i{font-style:normal;color:#b88cff}nav a{color:var(--muted);text-decoration:none;margin-left:24px}
.hero{text-align:center;padding:70px 0 45px}.pill{display:inline-block;border:1px solid #3b2a55;background:#171025;padding:7px 12px;border-radius:99px;color:#cbb6ff;font-weight:700;font-size:12px}.hero h1{font-size:clamp(42px,7vw,76px);line-height:.98;letter-spacing:-3px;margin:20px 0 16px}.grad{background:linear-gradient(90deg,#a78bfa,#f472b6);-webkit-background-clip:text;color:transparent}.hero p{max-width:650px;margin:auto;color:var(--muted);font-size:18px}
.box{margin:38px auto 0;background:rgba(16,18,26,.9);border:1px solid var(--line);border-radius:22px;padding:22px;box-shadow:0 25px 90px #0008;max-width:820px}.inputrow{display:flex;gap:10px}.inputrow input{flex:1;background:#080a10;color:white;border:1px solid #303442;border-radius:12px;padding:16px;font-size:15px;outline:none}.btn{border:0;border-radius:12px;padding:0 22px;font-weight:900;cursor:pointer}.primary{background:linear-gradient(90deg,var(--a),var(--b));color:white}.hint{color:var(--muted);font-size:12px;text-align:left;margin:10px 2px 0}.settings{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:15px}.select{background:#0a0c12;border:1px solid #282c38;border-radius:11px;padding:12px;text-align:left}.select span{display:block;color:var(--muted);font-size:11px;margin-bottom:5px}.select select{width:100%;background:transparent;color:white;border:0;outline:0}
.status{display:none;margin-top:18px;border-top:1px solid var(--line);padding-top:18px;text-align:left}.bar{height:8px;background:#242733;border-radius:99px;overflow:hidden}.fill{height:100%;width:0;background:linear-gradient(90deg,var(--a),var(--b));transition:width .5s}.statusline{display:flex;justify-content:space-between;margin-bottom:8px}.statusline small{color:var(--muted)}
section{padding:65px 0}.title{text-align:center;font-size:36px;margin:0 0 10px}.sub{text-align:center;color:var(--muted);max-width:650px;margin:0 auto 30px}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}.card{background:#0e1017;border:1px solid var(--line);border-radius:17px;padding:24px}.icon{font-size:28px}.card h3{margin:12px 0 7px}.card p{color:var(--muted);margin:0}.preview{display:grid;grid-template-columns:1fr 1fr;gap:20px;align-items:center}.phone{aspect-ratio:9/16;max-height:520px;margin:auto;width:min(290px,100%);background:linear-gradient(145deg,#17102b,#0b0d14);border:7px solid #20222c;border-radius:30px;padding:20px;display:flex;align-items:end;position:relative;overflow:hidden}.phone:before{content:"YOUR CLIP";position:absolute;top:25%;left:18px;right:18px;font-size:30px;font-weight:1000}.caption{background:#000b;padding:10px;border-radius:10px;font-weight:800;width:100%;text-align:center}.note{background:#0e1017;border:1px solid var(--line);padding:25px;border-radius:17px}.note li{margin:11px 0;color:#c5cad5}footer{border-top:1px solid var(--line);padding:25px 0;color:var(--muted);font-size:12px}
@media(max-width:700px){nav a{display:none}.inputrow{flex-direction:column}.btn{height:52px}.settings,.grid,.preview{grid-template-columns:1fr}.hero{padding-top:45px}.hero h1{letter-spacing:-2px}}
</style>
</head>
<body>
<nav class="wrap"><div class="logo">Clip<i>Forge</i></div><div><a href="#features">Features</a><a href="#how">How it works</a><a href="#about">About</a></div></nav>
<main>
<section class="hero wrap">
<span class="pill">AI-POWERED SHORT VIDEO WORKFLOW</span>
<h1>Turn long videos into<br><span class="grad">scroll-stopping Shorts.</span></h1>
<p>Paste a YouTube link, choose your format, and ClipForge prepares a vertical short-video project with smart captions and social-ready settings.</p>
<div class="box">
<div class="inputrow"><input id="url" placeholder="Paste YouTube video URL…"><button class="btn primary" onclick="start()">Create clips</button></div>
<div class="hint">Use videos you own or have permission to edit and republish.</div>
<div class="settings">
<div class="select"><span>FORMAT</span><select><option>9:16 Vertical</option><option>1:1 Square</option><option>16:9 Landscape</option></select></div>
<div class="select"><span>QUALITY</span><select><option>1080p</option><option>720p</option></select></div>
<div class="select"><span>CAPTIONS</span><select><option>Dynamic subtitles</option><option>Clean subtitles</option><option>Off</option></select></div>
</div>
<div class="status" id="status"><div class="statusline"><b id="st">Ready</b><small id="pct">0%</small></div><div class="bar"><div class="fill" id="fill"></div></div><p id="msg" style="color:#aeb4c1;margin-bottom:0">Waiting for a link…</p></div>
</div>
</section>

<section id="features"><div class="wrap"><h2 class="title">Everything for a clean short</h2><p class="sub">A simple workflow designed around the things creators actually need.</p>
<div class="grid">
<div class="card"><div class="icon">✂️</div><h3>Smart clip workflow</h3><p>Prepare multiple short-form segments from long-form content.</p></div>
<div class="card"><div class="icon">💬</div><h3>Readable subtitles</h3><p>Large, high-contrast captions designed for phone screens.</p></div>
<div class="card"><div class="icon">📱</div><h3>Vertical 9:16</h3><p>Social-ready framing for Shorts, Reels and TikTok-style feeds.</p></div>
<div class="card"><div class="icon">✨</div><h3>Clean output</h3><p>Consistent typography, spacing and export presets.</p></div>
<div class="card"><div class="icon">🎯</div><h3>Hook-focused</h3><p>Structure your clips around strong openings and concise moments.</p></div>
<div class="card"><div class="icon">⚡</div><h3>Fast workflow</h3><p>Paste a link, choose settings and send the job to your processing backend.</p></div>
</div></div></section>

<section id="how"><div class="wrap"><h2 class="title">How it works</h2><p class="sub">The interface is ready; connect it to your video-processing backend to make the pipeline live.</p>
<div class="preview"><div class="phone"><div class="caption">THIS IS HOW YOUR SUBTITLES CAN LOOK</div></div>
<div class="note"><h2>Production pipeline</h2><ul><li>Accept an authorized YouTube URL.</li><li>Fetch/process the source with your server-side media pipeline.</li><li>Detect strong moments and create vertical cuts.</li><li>Transcribe audio and burn styled subtitles.</li><li>Render high-quality MP4 files and return download links.</li></ul><p style="color:#8f96a5;font-size:13px">This front end intentionally does not pretend to process YouTube videos by itself. A secure backend/API is required for real downloading, transcription and rendering.</p></div></div></section>

<section id="about"><div class="wrap"><div class="box"><h2 style="margin-top:0">Built to become a real clipping SaaS</h2><p style="color:#aeb4c1">Connect this interface to a backend using FFmpeg + a transcription model + a job queue/storage layer. Add authentication and billing when you're ready.</p><a class="btn primary" href="#top" onclick="window.scrollTo({top:0,behavior:'smooth'});return false">Create a clip ↑</a></div></div></section>
</main>
<footer><div class="wrap">© 2026 ClipForge · Use only content you are authorized to process.</div></footer>
<script>
async function start(){
 const u=document.getElementById('url').value.trim(), s=document.getElementById('status');
 if(!u){alert('Paste a YouTube URL first.');return}
 s.style.display='block'; const fill=document.getElementById('fill'),pct=document.getElementById('pct'),st=document.getElementById('st'),msg=document.getElementById('msg');
 st.textContent='Starting'; msg.textContent='Submitting the clipping job…';
 try{
  const selects=[...document.querySelectorAll('.select select')];
  const r=await fetch('/api/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:u,format:selects[0].value,quality:selects[1].value,captions:selects[2].value})});
  if(!r.ok) throw new Error(await r.text()); const {job_id}=await r.json();
  const timer=setInterval(async()=>{
   const x=await (await fetch('/api/jobs/'+job_id)).json(); fill.style.width=(x.progress||0)+'%'; pct.textContent=(x.progress||0)+'%'; msg.textContent=x.message||''; st.textContent=x.state==='done'?'Complete':x.state==='error'?'Error':'Processing';
   if(x.state==='done'||x.state==='error'){clearInterval(timer); if(x.clips){msg.innerHTML=x.message+'<br>'+x.clips.map((c,i)=>`<a href="${c}" target="_blank" style="color:#cbb6ff;font-weight:800">Download clip ${i+1}</a>`).join(' · ');}}
  },1200);
 }catch(e){st.textContent='Error';msg.textContent=e.message||'Could not start the job.';fill.style.width='0%';pct.textContent='0%'}
}
</script>
</body>
</html>"""

@app.get('/', response_class=HTMLResponse)
def home():
    return INDEX_HTML
