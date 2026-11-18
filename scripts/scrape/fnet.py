import re,json,subprocess,sys,concurrent.futures as cf
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
C={"CN":"China","TW":"Taiwan","HK":"Hong Kong","SG":"Singapore","TH":"Thailand","MY":"Malaysia","US":"United States","JP":"Japan","KR":"South Korea","DE":"Germany","AU":"Australia","IN":"India","GB":"United Kingdom","NL":"Netherlands","BE":"Belgium","ID":"Indonesia","PH":"Philippines","AE":"United Arab Emirates","CA":"Canada","FR":"France","IT":"Italy","ES":"Spain","PL":"Poland","TR":"Turkey","BD":"Bangladesh","PK":"Pakistan","KH":"Cambodia","LK":"Sri Lanka"}
def get(u):
    try: return subprocess.run(["curl","-sSL","-m","25","-A",UA,u],capture_output=True,timeout=35).stdout.decode("utf-8","ignore")
    except Exception: return ""
def page(cc,p):
    t=get(f"https://www.freightnet.com/directory/p{p}/c{cc}/s30.htm")
    out=[]
    for blk in re.findall(r'<script type="application/ld\+json">(.*?)</script>',t,re.S):
        try: j=json.loads(blk)
        except Exception: continue
        for it in (j.get("itemListElement") or []) if isinstance(j,dict) else []:
            x=it.get("item",{}) if isinstance(it,dict) else {}
            if not isinstance(x,dict): continue
            a=x.get("address",{}) or {}
            if not isinstance(a,dict): a={}
            out.append(dict(name=x.get("name",""),web=x.get("sameAs",""),profile=x.get("url",""),country=C[cc],city=a.get("addressLocality",""),addr=a.get("streetAddress",""),phone=x.get("telephone",""),email="",services=[]))
    return out
res=[]
def run(cc):
    allr=[]
    for p in range(1,int(sys.argv[2]) if len(sys.argv)>2 else 15):
        r=page(cc,p)
        if not r: break
        allr+=r
    return cc,allr
with cf.ThreadPoolExecutor(8) as ex:
    for cc,r in ex.map(run,C):
        print(cc,len(r),sum(1 for x in r if x["web"]),file=sys.stderr); res+=r
json.dump(res,open(sys.argv[1],"w"),ensure_ascii=False); print(len(res),sum(1 for x in res if x["web"]))
