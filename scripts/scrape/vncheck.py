import re,html,json,subprocess,sys,urllib.parse,concurrent.futures as cf
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
VN=re.compile(r"(viet\s?nam|越南|ベトナム|베트남|ho chi minh|hochiminh|saigon|hai ?phong|ha ?noi|da ?nang|cat lai|cai mep)",re.I)
def get(u):
    try:
        r=subprocess.run(["curl","-sSL","-m","20","-A",UA,"-w","\n__EFF__%{url_effective}",u],capture_output=True,timeout=28).stdout.decode("utf-8","ignore")
        b,_,e=r.rpartition("\n__EFF__"); return b,e
    except Exception: return "",u
def text(b):
    t=re.sub(r"(?is)<(script|style|noscript|svg).*?</\1>"," ",b); t=html.unescape(re.sub(r"<[^>]+>"," ",t)); return re.sub(r"\s+"," ",t)
def check(c):
    home,eff=get(c["web"])
    if not home: return dict(c,status="dead")
    links=[]
    for m in re.finditer(r'<a[^>]+href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>',home,re.S|re.I):
        h,a=m.group(1),re.sub("<[^>]+>","",m.group(2))
        if VN.search(h) or VN.search(a):
            u=urllib.parse.urljoin(eff,h)
            if urllib.parse.urlparse(u).netloc.split(":")[0].removeprefix("www.")==urllib.parse.urlparse(eff).netloc.split(":")[0].removeprefix("www."):
                links.append(u)
    links=list(dict.fromkeys(links))[:2]
    pages=[(eff,text(home))]+[(u,text(get(u)[0])) for u in links]
    ev=[];urls=[]
    for u,t in pages:
        hits=[t[max(0,m.start()-120):m.end()+120] for m in VN.finditer(t)]
        if hits: urls.append(u); ev+=hits[:4]
    alltxt=" ".join(t for _,t in pages)
    emails=sorted(set(e.lower() for e in re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}",alltxt) if not re.search(r"\.(png|jpg|webp|svg|gif)$",e,re.I)))[:4]
    nets=sorted(set(k.upper() for k in re.findall(r"\b(WCA|JCtrans|PPL|WWPC|FIATA|IATA|BIFA|FMC|NVOCC|CIFA|GLA|AFIF|SFFLA|FMFF|TIFFA|CBFCA|NCBFAA)\b",alltxt,re.I)))
    low=alltxt.lower()
    exp=bool(re.search(r"(from|ex)[ -](viet ?nam|ho chi minh|hai ?phong|ha ?noi)|viet ?nam (to|→|-) ?(usa|us |uk|the|india|australia|europe|japan|korea|china|canada|germany)",low))
    imp=bool(re.search(r"(to|into)[ -](viet ?nam|ho chi minh|hai ?phong|ha ?noi)",low))
    office=bool(re.search(r"(office|branch|subsidiar\w*)[^.]{0,60}(viet ?nam|ho chi minh|ha ?noi|hai ?phong)",low))
    dedicated=any(VN.search(u) for u in urls)
    return dict(c,status="ok",vn_hits=len(ev),vn_urls=urls,vn_ev=ev[:5],emails_site=emails,nets=nets,exp=exp,imp=imp,office=office,dedicated=dedicated,eff=eff)
cands=json.load(open(sys.argv[1]))
out=[]
with cf.ThreadPoolExecutor(20) as ex:
    for i,r in enumerate(ex.map(check,cands)):
        out.append(r)
        if i%100==0: print(i,file=sys.stderr)
json.dump(out,open(sys.argv[2],"w"),ensure_ascii=False)
ok=[r for r in out if r.get("vn_hits")]
print("checked",len(out),"with VN",len(ok),"dedicated",sum(r["dedicated"] for r in ok))
