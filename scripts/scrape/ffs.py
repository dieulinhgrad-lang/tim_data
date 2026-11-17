import re,html,json,subprocess,sys,concurrent.futures as cf
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
PAGES={
"China":"China dalian guangzhou ningbo qingdao shekou shenzhen suzhou tianjin xiamen",
"Taiwan":"taiwan kaohsiung","Hong Kong":"hong-kong","Singapore":"singapore",
"Thailand":"thailand Laem-Chabang","Malaysia":"Malaysia port-klang tanjung-pelepas",
"United States":"california los-angeles port-of-long-beach New-York New-Jersey Texas houston Florida miami Illinois Chicago Georgia Washington san-francisco dallas-fort-worth san-diego port-of-baltimore port-of-charleston-south-carolina the-port-of-boston Pennsylvania Ohio Virginia Michigan",
"Japan":"japan Keihin kobe osaka nagoya shimizu","South Korea":"south-korea Busan",
"Germany":"germany hamburg frankfurt","Australia":"australia Brisbane melbourne",
"India":"india mundra pipavav magdalla","United Kingdom":"UK",
"Netherlands":"netherlands rotterdam","Belgium":"belgium antwerp",
"Philippines":"Philippines manila","Indonesia":"indonesia Tanjung-Priok","Cambodia":"Cambodia",
"Bangladesh":"Bangladesh","Pakistan":"pakistan","Sri Lanka":"sri-lanka Colombo",
"United Arab Emirates":"uae dubai jebel-ali sharjah","Canada":"canada calgary halifax",
"France":"france","Italy":"italy","Spain":"spain barcelona valencia","Poland":"poland","Turkey":"turkey mersin ambarli",
}
def get(u):
    try: return subprocess.run(["curl","-sSL","-m","30","-A",UA,u],capture_output=True,timeout=40).stdout.decode("utf-8","ignore")
    except Exception: return ""
def parse(t,country,slug):
    out=[]
    blocks=re.split(r"<p>\s*<strong>",t)
    for b in blocks[1:]:
        b=b.split("</ul>")[0]
        name=re.search(r"^(?:<b>)?(.*?)(?:</b>)?</strong>",b,re.S)
        web=re.search(r"Web:\s*<a href=\"(https?://[^\"]+)\"",b)
        if not (name and web): continue
        txt=html.unescape(re.sub(r"<br\s*/?>","\n",b)); txt=re.sub(r"<[^>]+>"," ",txt)
        ph=re.search(r"Ph:\s*([^\n]+)",txt); em=re.search(r"Email:\s*([^\n]+)",txt)
        lines=[l.strip() for l in txt.split("\n") if l.strip()]
        addr=" ".join(l for l in lines[1:5] if not l.startswith(("Web:","Ph:","Fx:","Email:")))
        svc=[l.strip() for l in re.findall(r"<li>(.*?)</li>",b,re.S)]
        out.append(dict(name=html.unescape(re.sub("<[^>]+>","",name.group(1))).strip(),web=web.group(1),country=country,page=slug,
             phone=ph.group(1).strip() if ph else "",email=em.group(1).strip() if em else "",addr=addr[:200],services=[re.sub("<[^>]+>","",s) for s in svc][:6]))
    return out
jobs=[(c,s) for c,ss in PAGES.items() for s in ss.split()]
res=[]
with cf.ThreadPoolExecutor(8) as ex:
    futs={ex.submit(get,"https://freightforwarderservices.com/"+s):(c,s) for c,s in jobs}
    for f in cf.as_completed(futs):
        c,s=futs[f]; r=parse(f.result(),c,s); res+=r; print(c,s,len(r),file=sys.stderr)
json.dump(res,open(sys.argv[1],"w"),ensure_ascii=False)
print(len(res))
