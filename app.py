"""
SongFinder - a Shazam-style app: listen -> identify -> get the song legally.

Setup:
    pip install flask requests
    export AUDD_API_TOKEN=your_token        # https://audd.io  (song recognition)
    export JAMENDO_CLIENT_ID=your_id        # https://devportal.jamendo.com (optional, free CC music)
    python app.py   ->  open http://localhost:5000

Note: the microphone only works on localhost or HTTPS.
"""
import os
import requests
from flask import Flask, request, jsonify, Response

app = Flask(__name__)
AUDD_TOKEN = os.getenv("AUDD_API_TOKEN", "")
JAMENDO_ID = os.getenv("JAMENDO_CLIENT_ID", "")


def itunes_lookup(title, artist):
    """Find a 30s preview + store link via the iTunes Search API."""
    r = requests.get(
        "https://itunes.apple.com/search",
        params={"term": f"{title} {artist}", "media": "music", "entity": "song", "limit": 1},
        timeout=10,
    ).json()
    if not r.get("results"):
        return {}
    t = r["results"][0]
    return {
        "preview_url": t.get("previewUrl"),
        "buy_url": t.get("trackViewUrl"),
        "artwork": (t.get("artworkUrl100") or "").replace("100x100", "400x400"),
    }


def jamendo_lookup(title, artist):
    """If the track is on Jamendo (Creative Commons), return a full-song download link."""
    if not JAMENDO_ID:
        return None
    r = requests.get(
        "https://api.jamendo.com/v3.0/tracks/",
        params={
            "client_id": JAMENDO_ID, "format": "json", "limit": 1,
            "namesearch": title, "artist_name": artist, "audiodownload_allowed": "true",
        },
        timeout=10,
    ).json()
    results = r.get("results", [])
    return results[0].get("audiodownload") if results else None


def _norm(s):
    return "".join(c for c in (s or "").lower() if c.isalnum() or c == " ").strip()


def audius_lookup(title, artist):
    """Full-length stream (and download, if the artist allows it) from Audius."""
    r = requests.get(
        "https://api.audius.co/v1/tracks/search",
        params={"query": f"{title} {artist}", "app_name": "SongFinder", "limit": 5},
        timeout=10,
    ).json()
    for t in r.get("data", []):
        same_title = _norm(title) in _norm(t.get("title"))
        same_artist = _norm(artist) in _norm((t.get("user") or {}).get("name")) or \
                      _norm((t.get("user") or {}).get("name")) in _norm(artist)
        if same_title and same_artist:
            tid = t["id"]
            base = "https://api.audius.co/v1/tracks/" + tid
            return {
                "stream_url": f"{base}/stream?app_name=SongFinder",
                "download_url": f"{base}/download?app_name=SongFinder"
                                if t.get("is_downloadable") else None,
                "page_url": "https://audius.co" + t.get("permalink", ""),
            }
    return None


def archive_lookup(title, artist):
    """Link to a matching recording on the Internet Archive (public domain / free licences)."""
    r = requests.get(
        "https://archive.org/advancedsearch.php",
        params={
            "q": f'title:("{title}") AND creator:("{artist}") AND mediatype:audio',
            "fl[]": "identifier", "rows": 1, "output": "json",
        },
        timeout=10,
    ).json()
    docs = r.get("response", {}).get("docs", [])
    return "https://archive.org/details/" + docs[0]["identifier"] if docs else None


def safe(fn, *args):
    """A failing source should never break the whole result."""
    try:
        return fn(*args)
    except Exception:
        return None


def build_result(title, artist, extra=None):
    out = {"title": title, "artist": artist, **(extra or {})}
    out.update(safe(itunes_lookup, title, artist) or {})
    out["free_full_download"] = safe(jamendo_lookup, title, artist)
    out["audius"] = safe(audius_lookup, title, artist)
    out["archive_url"] = safe(archive_lookup, title, artist)
    return out


@app.post("/api/identify")
def identify():
    """Audio blob -> AudD recognition -> download options."""
    audio = request.files.get("audio")
    if not audio:
        return jsonify(error="No audio received"), 400
    if not AUDD_TOKEN:
        return jsonify(error="Set AUDD_API_TOKEN on the server"), 500
    r = requests.post(
        "https://api.audd.io/",
        data={"api_token": AUDD_TOKEN, "return": "apple_music,spotify"},
        files={"file": ("clip.m4a" if "mp4" in (audio.mimetype or "") else "clip.webm", audio.stream, audio.mimetype)},
        timeout=30,
    ).json()
    res = r.get("result")
    if not res:
        return jsonify(error="Couldn't recognise that one. Try again closer to the speaker."), 404
    spotify = (res.get("spotify") or {}).get("external_urls", {}).get("spotify")
    return jsonify(build_result(res["title"], res["artist"], {"spotify_url": spotify}))


@app.get("/api/search")
def search():
    """Manual text search, same download options."""
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify(error="Empty query"), 400
    r = requests.get(
        "https://itunes.apple.com/search",
        params={"term": q, "media": "music", "entity": "song", "limit": 1}, timeout=10,
    ).json()
    if not r.get("results"):
        return jsonify(error="No results"), 404
    t = r["results"][0]
    return jsonify(build_result(t["trackName"], t["artistName"]))


@app.get("/api/download")
def download():
    """Proxy the preview so the browser saves it instead of playing it."""
    url = request.args.get("url", "")
    if not url.startswith("https://audio-ssl.itunes.apple.com/"):
        return "Invalid URL", 400
    upstream = requests.get(url, stream=True, timeout=20)
    name = request.args.get("name", "preview").replace('"', "")
    return Response(
        upstream.iter_content(8192),
        content_type=upstream.headers.get("Content-Type", "audio/mp4"),
        headers={"Content-Disposition": f'attachment; filename="{name}-preview.m4a"'},
    )


PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>SongFinder</title>
<style>
 body{font-family:system-ui,sans-serif;background:#0f1220;color:#eef;margin:0;display:flex;
      flex-direction:column;align-items:center;padding:32px 16px}
 h1{margin:0 0 24px}
 #btn{width:160px;height:160px;border-radius:50%;border:0;font-size:18px;color:#fff;cursor:pointer;
      background:linear-gradient(135deg,#6d5dfc,#3ec6ff);box-shadow:0 0 40px #6d5dfc66}
 #btn.on{animation:pulse 1s infinite}
 @keyframes pulse{50%{transform:scale(1.08)}}
 #status{margin:20px;min-height:24px;opacity:.8}
 .row{display:flex;gap:8px;margin-bottom:24px}
 input{padding:10px;border-radius:8px;border:0;width:220px}
 .sm{padding:10px 14px;border-radius:8px;border:0;background:#2a2f4a;color:#fff;cursor:pointer}
 #card{display:none;background:#1a1f36;border-radius:16px;padding:20px;max-width:360px;width:100%;text-align:center}
 #card img{width:200px;border-radius:12px}
 a.dl{display:block;margin:8px 0;padding:10px;border-radius:8px;background:#2a2f4a;color:#fff;text-decoration:none}
 a.dl.free{background:#1f8a5b}
 audio{width:100%;margin-top:8px}
</style></head><body>
<h1>SongFinder</h1>
<button id="btn">Tap to listen</button>
<div id="status"></div>
<div class="row"><input id="q" placeholder="...or type a song"><button class="sm" id="go">Search</button></div>
<div id="card"></div>
<script>
const $=id=>document.getElementById(id), btn=$('btn'), st=$('status'), card=$('card');
btn.onclick=async()=>{
  try{
    const stream=await navigator.mediaDevices.getUserMedia({audio:true});
    const rec=new MediaRecorder(stream), chunks=[];
    rec.ondataavailable=e=>chunks.push(e.data);
    rec.onstop=async()=>{
      stream.getTracks().forEach(t=>t.stop()); btn.classList.remove('on');
      st.textContent='Identifying...';
      const fd=new FormData(); fd.append('audio',new Blob(chunks,{type:rec.mimeType}),rec.mimeType.includes('mp4')?'clip.m4a':'clip.webm');
      show(await (await fetch('/api/identify',{method:'POST',body:fd})).json());
    };
    rec.start(); btn.classList.add('on'); st.textContent='Listening (15s)...';
    setTimeout(()=>rec.stop(),15000);
  }catch(e){st.textContent='Microphone access denied.';}
};
$('go').onclick=async()=>{
  st.textContent='Searching...';
  show(await (await fetch('/api/search?q='+encodeURIComponent($('q').value))).json());
};
function show(d){
  if(d.error){st.textContent=d.error;card.style.display='none';return;}
  st.textContent='';
  const nm=encodeURIComponent(d.title+' - '+d.artist);
  card.innerHTML=
    (d.artwork?`<img src="${d.artwork}">`:'')+
    `<h2>${d.title}</h2><p>${d.artist}</p>`+
    (d.preview_url?`<audio controls src="${d.preview_url}"></audio>
      <a class="dl" href="/api/download?url=${encodeURIComponent(d.preview_url)}&name=${nm}">Download 30s preview</a>`:'')+
    (d.free_full_download?`<a class="dl free" href="${d.free_full_download}">Download full song (free, Creative Commons)</a>`:'')+
    (d.audius?`<p style="margin:12px 0 4px">Full song on Audius</p>
      <audio controls src="${d.audius.stream_url}"></audio>`+
      (d.audius.download_url?`<a class="dl free" href="${d.audius.download_url}">Download full song (artist allows it)</a>`:'')+
      `<a class="dl" href="${d.audius.page_url}" target="_blank">Open on Audius</a>`:'')+
    (d.archive_url?`<a class="dl free" href="${d.archive_url}" target="_blank">Free recording on Internet Archive</a>`:'')+
    (!d.audius&&!d.free_full_download&&!d.archive_url?`<p style="opacity:.7;font-size:14px">No free full-length version found. Use the preview or buy link.</p>`:'')+
    (d.buy_url?`<a class="dl" href="${d.buy_url}" target="_blank">Buy / stream on Apple Music</a>`:'')+
    (d.spotify_url?`<a class="dl" href="${d.spotify_url}" target="_blank">Open in Spotify</a>`:'');
  card.style.display='block';
}
</script></body></html>"""


@app.get("/")
def index():
    return PAGE


if __name__ == "__main__":
    app.run(debug=True)
