import glob, json, os, urllib.request, urllib.parse, time, re
UA="ec-monitor landmark test/0.1 (www.saccani.net/conspicuity-monitor)"
cache=json.load(open("sitelinks.json")) if os.path.exists("sitelinks.json") else {}
ids=set()
for fn in glob.glob("tile_*.csv"):
    with open(fn,encoding="utf-8") as f:
        h=f.readline().rstrip("\n").split("\t")
        for line in f:
            r=dict(zip(h,line.rstrip("\n").split("\t")))
            q=r.get("wikidata","").strip()
            if re.fullmatch(r"Q\d+",q) and q not in cache: ids.add(q)
ids=sorted(ids); print("to fetch",len(ids),flush=True)
t0=time.time()
for i in range(0,len(ids),300):
    chunk=ids[i:i+300]
    q="SELECT ?i ?n WHERE { VALUES ?i { %s } ?i wikibase:sitelinks ?n }"%" ".join("wd:"+x for x in chunk)
    for a in range(5):
        try:
            req=urllib.request.Request("https://query.wikidata.org/sparql",data=urllib.parse.urlencode({"query":q,"format":"json"}).encode(),headers={"User-Agent":UA,"Accept":"application/sparql-results+json"})
            res=json.load(urllib.request.urlopen(req,timeout=120))
            for b in res["results"]["bindings"]:
                cache[b["i"]["value"].rsplit("/",1)[1]]=int(b["n"]["value"])
            for x in chunk: cache.setdefault(x,0)
            break
        except Exception as e:
            print("retry",e,flush=True); time.sleep(20)
    time.sleep(1)
json.dump(cache,open("sitelinks.json","w"))
print("done",len(cache),f"{time.time()-t0:.0f}s")
