import re,json,subprocess,sys,urllib.parse,concurrent.futures as cf
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
VN=re.compile(r"(viet-?nam|vietnam|hochiminh|ho-chi-minh|haiphong|hai-phong|hanoi|%e8%b6%8a%e5%8d%97)",re.I)
def get(u):
    try: return subprocess.run(["curl","-sSL","-m","15","-A",UA,u],capture_output=True,timeout=20).stdout.decode("utf-8","ignore")
    except Exception: return ""
def run(r):
    base=r.get("eff") or r["web"]; p=urllib.parse.urlparse(base); root=f"{p.scheme}://{p.netloc}"
    seen=set(); queue=[root+"/sitemap.xml",root+"/sitemap_index.xml",root+"/wp-sitemap.xml"]; hits=[]; n=0
    while queue and n<8:
        u=queue.pop(0)
        if u in seen: continue
        seen.add(u); n+=1
        t=get(u)
        if "<loc>" not in t: continue
        locs=re.findall(r"<loc>\s*([^<\s]+)\s*</loc>",t)
        for l in locs:
            if l.endswith(".xml") or "sitemap" in l.split("/")[-1]:
                if len(queue)<10: queue.append(l)
            elif VN.search(l): hits.append(l)
        if hits: break
    return dict(domain=r["domain"],sm_hits=hits[:5])
d=[r for r in json.load(open(sys.argv[1])) if r.get("status")=="ok" and not r.get("vn_hits")]
out=[]
with cf.ThreadPoolExecutor(24) as ex:
    for i,x in enumerate(ex.map(run,d)):
        if x["sm_hits"]: out.append(x)
        if i%200==0: print(i,file=sys.stderr)
json.dump(out,open(sys.argv[2],"w"))
print("sites",len(d),"with vn urls",len(out))
