import csv, json, requests, concurrent.futures as cf
BASE="https://monastudiopro.com"
UA=("Mozilla/5.0 (Linux; Android 14; V2405A) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36")
FILES=["accounts.csv","accounts10.csv","accounts_new7.csv"]
def hdr(t): return {"Authorization":"Bearer "+t,"User-Agent":UA,"Accept":"application/json",
                    "Origin":BASE,"Referer":BASE+"/?module=canvas"}
rows=[]
for f in FILES:
    for a in csv.DictReader(open(f,encoding="utf-8")):
        rows.append((f,a["email"],a["token"]))
def one(it):
    f,e,t=it
    try:
        r=requests.get(BASE+"/api/auth/user",headers=hdr(t),timeout=25)
        j=r.json().get("data") or {}
        return (f,e,j.get("credits"),j.get("plan") or j.get("subscription"),r.status_code)
    except Exception as ex:
        return (f,e,None,None,f"ERR {type(ex).__name__}")
with cf.ThreadPoolExecutor(max_workers=6) as ex:
    res=list(ex.map(one,rows))
tot=0; bad=[]
for f,e,c,p,s in res:
    print(f"{f:18s} {e:24s} credits={c} plan={p} http={s}")
    if isinstance(c,(int,float)): tot+=c
    else: bad.append((e,s))
print("\n总数:",len(res)," 积分合计:",tot," 失败:",len(bad))
for b in bad: print("  失败",b)
